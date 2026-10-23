#include "semantics/type_checker.h"
#include "parser/tree_sitter_loader.h"
#include "utils/logger.h"

namespace javalsp {

std::string TypeChecker::getNodeText(TSNode node, const std::string& src) {
    if (ts_node_is_null(node)) return "";
    uint32_t start = ts_node_start_byte(node);
    uint32_t end = ts_node_end_byte(node);
    if (start >= src.size() || end > src.size() || start >= end) return "";
    return src.substr(start, end - start);
}

Range TypeChecker::getNodeRange(TSNode node) {
    if (ts_node_is_null(node)) return Range{{0, 0}, {0, 0}};
    TSPoint s = ts_node_start_point(node);
    TSPoint e = ts_node_end_point(node);
    return Range{
        {static_cast<int>(s.row), static_cast<int>(s.column)},
        {static_cast<int>(e.row), static_cast<int>(e.column)}
    };
}

JavaTypeRef TypeChecker::resolveExpressionType(
    const std::string& expr,
    std::shared_ptr<Scope> scope,
    const JavaCompilationUnit& cu,
    const SymbolTable& symTab) {

    if (expr.empty()) return JavaTypeRef{};

    // Handle chained expression: receiver.method(...) or receiver.field
    size_t lastDot = expr.find_last_of('.');
    if (lastDot != std::string::npos) {
        std::string base = expr.substr(0, lastDot);
        std::string member = expr.substr(lastDot + 1);

        // Strip method call parens if any: e.g. listUsers() -> listUsers
        bool isCall = false;
        if (!member.empty() && member.back() == ')') {
            isCall = true;
            size_t paren = member.find('(');
            if (paren != std::string::npos) {
                member = member.substr(0, paren);
            }
        }

        JavaTypeRef baseType = resolveExpressionType(base, scope, cu, symTab);
        if (baseType.name.empty()) {
            // Check if base is a class name: e.g. List
            const JavaClass* baseCls = symTab.resolveClass(base, cu);
            if (baseCls) {
                baseType.name = baseCls->name;
                baseType.fqn = baseCls->fqn;
            }
        }

        const JavaClass* cls = symTab.resolveClass(baseType.name, cu);
        if (!cls && !baseType.fqn.empty()) {
            cls = symTab.findClassByFqn(baseType.fqn);
        }

        if (cls) {
            for (const auto& m : cls->methods) {
                if (m.name == member) {
                    JavaTypeRef ret = m.returnType;
                    // Generic substitution
                    if (!baseType.typeArguments.empty()) {
                        if (ret.name == "E" || ret.name == "T") {
                            return baseType.typeArguments[0];
                        }
                        if (ret.name == "Stream" || ret.name == "List") {
                            if (!ret.typeArguments.empty() && (ret.typeArguments[0].name == "E" || ret.typeArguments[0].name == "T")) {
                                ret.typeArguments[0] = baseType.typeArguments[0];
                            }
                        }
                    }
                    return ret;
                }
            }
            for (const auto& f : cls->fields) {
                if (f.name == member) {
                    return f.type;
                }
            }
        }
    }

    // Simple identifier lookup
    if (scope) {
        auto var = scope->lookupVariable(expr);
        if (var) return var->type;
        LOG_INFO("resolveExpressionType: lookupVariable('" + expr + "') failed in scope (vars: " + std::to_string(scope->localVariables().size()) + ")");
    } else {
        LOG_INFO("resolveExpressionType: scope is null!");
    }

    // Check class in compilation unit
    for (const auto& cls : cu.classes) {
        for (const auto& f : cls.fields) {
            if (f.name == expr) return f.type;
        }
    }

    // Check classes in symbol table
    if (!cu.packageName.empty()) {
        for (const auto* cls : symTab.getClassesInPackage(cu.packageName)) {
            for (const auto& f : cls->fields) {
                if (f.name == expr) return f.type;
            }
        }
    }

    // Check if it's a known class name
    const JavaClass* resolvedCls = symTab.resolveClass(expr, cu);
    if (resolvedCls) {
        return JavaTypeRef{resolvedCls->name, resolvedCls->fqn};
    }

    return JavaTypeRef{};
}

std::vector<Diagnostic> TypeChecker::check(
    const JavaCompilationUnit& cu,
    const std::string& src,
    TSNode rootNode,
    const SymbolTable& symTab) {

    std::vector<Diagnostic> diags;
    auto rootScope = std::make_shared<Scope>(ScopeKind::FILE);

    if (ts_node_is_null(rootNode)) {
        TSParser* parser = TreeSitterLoader::instance().newParser();
        if (parser) {
            TSTree* tree = ts_parser_parse_string(parser, nullptr, src.c_str(), src.size());
            if (tree) {
                TSNode r = ts_tree_root_node(tree);
                collectDiagnostics(r, src, cu, symTab, rootScope, diags);
                ts_tree_delete(tree);
            }
            ts_parser_delete(parser);
        }
    } else {
        collectDiagnostics(rootNode, src, cu, symTab, rootScope, diags);
    }

    return diags;
}

static bool isTypeDeclarationOrImport(TSNode node) {
    TSNode cur = node;
    while (!ts_node_is_null(cur)) {
        std::string t = ts_node_type(cur);
        if (t == "import_declaration" || t == "package_declaration") {
            return true;
        }
        if (t == "type_parameters" || t == "type_parameter") {
            return true;
        }
        TSNode parent = ts_node_parent(cur);
        if (!ts_node_is_null(parent)) {
            std::string pt = ts_node_type(parent);
            if (pt == "class_declaration" || pt == "interface_declaration" ||
                pt == "enum_declaration" || pt == "record_declaration" ||
                pt == "annotation_type_declaration") {
                TSNode nameNode = ts_node_child_by_field_name(parent, "name", 4);
                if (!ts_node_is_null(nameNode) && ts_node_eq(nameNode, cur)) {
                    return true;
                }
            }
        }
        cur = parent;
    }
    return false;
}

static bool isDeclaredTypeParameter(TSNode node, const std::string& typeName, const std::string& src) {
    TSNode cur = node;
    while (!ts_node_is_null(cur)) {
        TSNode params = ts_node_child_by_field_name(cur, "type_parameters", 15);
        if (!ts_node_is_null(params)) {
            for (uint32_t i = 0; i < ts_node_child_count(params); ++i) {
                TSNode p = ts_node_child(params, i);
                if (std::string(ts_node_type(p)) == "type_parameter") {
                    TSNode nameNode = ts_node_child_by_field_name(p, "name", 4);
                    if (ts_node_is_null(nameNode)) {
                        for (uint32_t j = 0; j < ts_node_child_count(p); ++j) {
                            if (std::string(ts_node_type(ts_node_child(p, j))) == "type_identifier") {
                                nameNode = ts_node_child(p, j);
                                break;
                            }
                        }
                    }
                    if (!ts_node_is_null(nameNode)) {
                        uint32_t s = ts_node_start_byte(nameNode);
                        uint32_t e = ts_node_end_byte(nameNode);
                        if (s < src.size() && e <= src.size() && src.substr(s, e - s) == typeName) {
                            return true;
                        }
                    }
                }
            }
        }
        cur = ts_node_parent(cur);
    }
    return false;
}

static bool findTypeInClassHierarchy(const JavaClass& cls, const std::string& typeName) {
    if (cls.name == typeName || cls.fqn == typeName) return true;

    std::string normFqn = cls.fqn;
    std::replace(normFqn.begin(), normFqn.end(), '$', '.');
    std::string normType = typeName;
    std::replace(normType.begin(), normType.end(), '$', '.');

    if (normFqn == normType || cls.name == normType) return true;

    if (normFqn.size() > normType.size() &&
        normFqn.compare(normFqn.size() - normType.size(), normType.size(), normType) == 0 &&
        normFqn[normFqn.size() - normType.size() - 1] == '.') {
        return true;
    }
    for (const auto& inner : cls.innerClasses) {
        if (findTypeInClassHierarchy(inner, typeName)) return true;
    }
    return false;
}

static bool isPartOfScopedTypeIdentifier(TSNode node) {
    TSNode cur = ts_node_parent(node);
    while (!ts_node_is_null(cur)) {
        std::string pt = ts_node_type(cur);
        if (pt == "scoped_type_identifier") {
            return true;
        }
        if (pt == "generic_type" || pt == "type_arguments" || pt == "formal_parameters" ||
            pt == "variable_declarator" || pt == "local_variable_declaration" ||
            pt == "field_declaration" || pt == "method_declaration" || pt == "class_declaration" ||
            pt == "interface_declaration") {
            break;
        }
        cur = ts_node_parent(cur);
    }
    return false;
}

void TypeChecker::collectDiagnostics(
    TSNode node,
    const std::string& src,
    const JavaCompilationUnit& cu,
    const SymbolTable& symTab,
    std::shared_ptr<Scope> currentScope,
    std::vector<Diagnostic>& diags) {

    if (ts_node_is_null(node)) return;

    std::string type = ts_node_type(node);

    // Check for unresolved annotation types
    if (type == "annotation" || type == "marker_annotation") {
        TSNode nameNode = ts_node_child_by_field_name(node, "name", 4);
        if (!ts_node_is_null(nameNode)) {
            std::string annotType = getNodeText(nameNode, src);
            if (!annotType.empty() && annotType != "SuppressWarnings") {
                const JavaClass* resolved = symTab.resolveClass(annotType, cu);
                if (!resolved) {
                    bool foundLocal = false;
                    for (const auto& c : cu.classes) {
                        if (findTypeInClassHierarchy(c, annotType)) {
                            foundLocal = true;
                            break;
                        }
                    }
                    if (!foundLocal) {
                        Diagnostic d;
                        d.range = getNodeRange(nameNode);
                        d.severity = 1; // Error
                        d.source = "java-lsp";
                        d.message = annotType + " cannot be resolved to a type";
                        diags.push_back(d);
                    }
                }
            }
        }
    }

    // Check for unresolved scoped_type_identifier (e.g. java.util.StringTokenizer or Outer.Inner)
    if (type == "scoped_type_identifier") {
        if (!isPartOfScopedTypeIdentifier(node)) {
            if (!isTypeDeclarationOrImport(node)) {
                std::string typeName = getNodeText(node, src);
                if (!isDeclaredTypeParameter(node, typeName, src)) {
                    const JavaClass* resolved = symTab.resolveClass(typeName, cu);
                    if (!resolved) {
                        bool foundLocal = false;
                        for (const auto& c : cu.classes) {
                            if (findTypeInClassHierarchy(c, typeName)) {
                                foundLocal = true;
                                break;
                            }
                        }
                        if (!foundLocal) {
                            Diagnostic d;
                            d.range = getNodeRange(node);
                            d.severity = 1; // Error
                            d.source = "java-lsp";
                            d.message = typeName + " cannot be resolved to a type";
                            diags.push_back(d);
                        }
                    }
                }
            }
        }
    }

    // Check for unresolved type_identifier
    if (type == "type_identifier") {
        if (!isPartOfScopedTypeIdentifier(node) && !isTypeDeclarationOrImport(node)) {
            std::string typeName = getNodeText(node, src);
            // Exclude primitive wrappers, void, var, or single-letter type variables
            if (typeName != "void" && typeName != "var" &&
                !(typeName.size() <= 2 && std::isupper(typeName[0]))) {

                if (!isDeclaredTypeParameter(node, typeName, src)) {
                    const JavaClass* resolved = symTab.resolveClass(typeName, cu);
                    if (!resolved) {
                        bool foundLocal = false;
                        for (const auto& c : cu.classes) {
                            if (findTypeInClassHierarchy(c, typeName)) {
                                foundLocal = true;
                                break;
                            }
                        }
                        if (!foundLocal) {
                            Diagnostic d;
                            d.range = getNodeRange(node);
                            d.severity = 1; // Error
                            d.source = "java-lsp";
                            d.message = typeName + " cannot be resolved to a type";
                            diags.push_back(d);
                        }
                    }
                }
            }
        }
    }

    uint32_t count = ts_node_child_count(node);
    for (uint32_t i = 0; i < count; ++i) {
        collectDiagnostics(ts_node_child(node, i), src, cu, symTab, currentScope, diags);
    }
}

} // namespace javalsp
