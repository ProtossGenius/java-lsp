#include "parser/java_parser.h"
#include "parser/tree_sitter_loader.h"
#include "utils/logger.h"
#include "utils/fs_utils.h"
#include <cstring>
#include <algorithm>

namespace javalsp {

JavaParser::JavaParser() {
    init();
}

JavaParser::~JavaParser() {
    if (parser_) {
        ts_parser_delete(parser_);
        parser_ = nullptr;
    }
}

bool JavaParser::init() {
    if (parser_) return true;
    if (!TreeSitterLoader::instance().initialize()) {
        return false;
    }
    parser_ = TreeSitterLoader::instance().newParser();
    return parser_ != nullptr;
}

std::string JavaParser::getNodeText(TSNode node, const std::string& src) const {
    if (ts_node_is_null(node)) return "";
    uint32_t start = ts_node_start_byte(node);
    uint32_t end = ts_node_end_byte(node);
    if (start >= src.size() || end > src.size() || start >= end) return "";
    return src.substr(start, end - start);
}

Range JavaParser::getNodeRange(TSNode node) const {
    if (ts_node_is_null(node)) return Range{{0, 0}, {0, 0}};
    TSPoint s = ts_node_start_point(node);
    TSPoint e = ts_node_end_point(node);
    return Range{
        {static_cast<int>(s.row), static_cast<int>(s.column)},
        {static_cast<int>(e.row), static_cast<int>(e.column)}
    };
}

JavaTypeRef JavaParser::parseType(TSNode node, const std::string& src) const {
    JavaTypeRef ref;
    if (ts_node_is_null(node)) return ref;

    std::string typeStr = ts_node_type(node);
    if (typeStr == "generic_type") {
        TSNode base = ts_node_child_by_field_name(node, "type", 4);
        if (ts_node_is_null(base)) {
            base = ts_node_child(node, 0);
        }
        ref.name = getNodeText(base, src);

        TSNode args = ts_node_child_by_field_name(node, "type_arguments", 14);
        if (ts_node_is_null(args)) {
            for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
                TSNode c = ts_node_child(node, i);
                if (std::string(ts_node_type(c)) == "type_arguments") {
                    args = c;
                    break;
                }
            }
        }
        if (!ts_node_is_null(args)) {
            for (uint32_t i = 0; i < ts_node_child_count(args); ++i) {
                TSNode arg = ts_node_child(args, i);
                std::string atype = ts_node_type(arg);
                if (atype == "type_identifier" || atype == "generic_type" || atype == "wildcard") {
                    ref.typeArguments.push_back(parseType(arg, src));
                }
            }
        }
    } else if (typeStr == "array_type") {
        TSNode elem = ts_node_child_by_field_name(node, "element", 7);
        if (ts_node_is_null(elem)) elem = ts_node_child(node, 0);
        ref = parseType(elem, src);
        ref.isArray = true;
    } else {
        ref.name = getNodeText(node, src);
        if (typeStr == "integral_type" || typeStr == "floating_point_type" ||
            typeStr == "boolean_type" || typeStr == "void_type") {
            ref.isPrimitive = true;
        }
    }
    return ref;
}

std::vector<std::string> JavaParser::parseModifiersAndAnnotations(
    TSNode node, const std::string& src, std::vector<std::string>& annotations) const {
    std::vector<std::string> modifiers;
    if (ts_node_is_null(node)) return modifiers;

    for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
        TSNode c = ts_node_child(node, i);
        std::string ctype = ts_node_type(c);
        if (ctype == "marker_annotation" || ctype == "annotation") {
            std::string text = getNodeText(c, src);
            if (!text.empty()) {
                if (text[0] == '@') text = text.substr(1);
                annotations.push_back(text);
            }
        } else if (ctype == "modifiers") {
            parseModifiersAndAnnotations(c, src, annotations);
        } else {
            std::string mod = getNodeText(c, src);
            if (!mod.empty() && mod[0] != '@') {
                modifiers.push_back(mod);
            }
        }
    }
    return modifiers;
}

std::shared_ptr<JavaCompilationUnit> JavaParser::parse(const std::string& sourceText, const std::string& filePath) {
    if (!init()) return nullptr;

    TSTree* tree = ts_parser_parse_string(parser_, nullptr, sourceText.c_str(), sourceText.size());
    if (!tree) return nullptr;

    auto cu = std::make_shared<JavaCompilationUnit>();
    cu->filePath = filePath;
    cu->uri = fs::pathToUri(filePath);
    cu->sourceText = sourceText;

    TSNode root = ts_tree_root_node(tree);
    parseNode(root, sourceText, *cu);

    ts_tree_delete(tree);
    return cu;
}

void JavaParser::parseNode(TSNode node, const std::string& src, JavaCompilationUnit& cu) {
    uint32_t count = ts_node_child_count(node);
    for (uint32_t i = 0; i < count; ++i) {
        TSNode child = ts_node_child(node, i);
        std::string type = ts_node_type(child);

        if (type == "package_declaration") {
            parsePackage(child, src, cu);
        } else if (type == "import_declaration") {
            parseImport(child, src, cu);
        } else if (type == "class_declaration" || type == "interface_declaration" ||
                   type == "enum_declaration" || type == "record_declaration") {
            parseClass(child, src, cu);
        } else if (type == "ERROR") {
            // Traverse inside ERROR nodes to find embedded declarations
            parseNode(child, src, cu);
        }
    }
}

void JavaParser::parsePackage(TSNode node, const std::string& src, JavaCompilationUnit& cu) {
    for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
        TSNode c = ts_node_child(node, i);
        std::string type = ts_node_type(c);
        if (type == "scoped_identifier" || type == "identifier") {
            cu.packageName = getNodeText(c, src);
            return;
        }
    }
}

void JavaParser::parseImport(TSNode node, const std::string& src, JavaCompilationUnit& cu) {
    for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
        TSNode c = ts_node_child(node, i);
        std::string type = ts_node_type(c);
        if (type == "scoped_identifier" || type == "identifier") {
            std::string imp = getNodeText(c, src);
            // Check if followed by .*
            if (i + 2 < ts_node_child_count(node)) {
                std::string next = ts_node_type(ts_node_child(node, i + 1));
                std::string after = ts_node_type(ts_node_child(node, i + 2));
                if (next == "." && after == "asterisk") {
                    imp += ".*";
                }
            }
            cu.imports.push_back(imp);
            return;
        }
    }
}

void JavaParser::parseClass(TSNode node, const std::string& src, JavaCompilationUnit& cu, JavaClass* parentClass) {
    JavaClass cls;
    cls.range = getNodeRange(node);
    std::string type = ts_node_type(node);

    if (type == "interface_declaration") cls.kind = ClassKind::INTERFACE;
    else if (type == "enum_declaration") cls.kind = ClassKind::ENUM;
    else if (type == "record_declaration") cls.kind = ClassKind::RECORD;
    else cls.kind = ClassKind::CLASS;

    TSNode bodyNode{};

    for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
        TSNode c = ts_node_child(node, i);
        std::string ctype = ts_node_type(c);

        if (ctype == "modifiers") {
            parseModifiersAndAnnotations(c, src, cls.annotations);
            for (uint32_t j = 0; j < ts_node_child_count(c); ++j) {
                TSNode m = ts_node_child(c, j);
                std::string mtype = ts_node_type(m);
                if (mtype != "marker_annotation" && mtype != "annotation") {
                    std::string mtext = getNodeText(m, src);
                    if (!mtext.empty()) cls.modifiers.push_back(mtext);
                }
            }
        } else if (ctype == "identifier") {
            cls.name = getNodeText(c, src);
            cls.nameRange = getNodeRange(c);
        } else if (ctype == "superclass") {
            for (uint32_t j = 0; j < ts_node_child_count(c); ++j) {
                TSNode s = ts_node_child(c, j);
                std::string stype = ts_node_type(s);
                if (stype == "type_identifier" || stype == "generic_type") {
                    cls.extendsType = parseType(s, src);
                    break;
                }
            }
        } else if (ctype == "super_interfaces") {
            for (uint32_t j = 0; j < ts_node_child_count(c); ++j) {
                TSNode s = ts_node_child(c, j);
                std::string stype = ts_node_type(s);
                if (stype == "type_list") {
                    for (uint32_t k = 0; k < ts_node_child_count(s); ++k) {
                        TSNode t = ts_node_child(s, k);
                        std::string ttype = ts_node_type(t);
                        if (ttype == "type_identifier" || ttype == "generic_type") {
                            cls.implementsTypes.push_back(parseType(t, src));
                        }
                    }
                } else if (stype == "type_identifier" || stype == "generic_type") {
                    cls.implementsTypes.push_back(parseType(s, src));
                }
            }
        } else if (ctype == "class_body" || ctype == "interface_body" || ctype == "enum_body") {
            bodyNode = c;
        }
    }

    cls.packageName = cu.packageName;
    if (parentClass) {
        cls.fqn = parentClass->fqn + "." + cls.name;
    } else {
        cls.fqn = cls.packageName.empty() ? cls.name : (cls.packageName + "." + cls.name);
    }

    if (!ts_node_is_null(bodyNode)) {
        for (uint32_t i = 0; i < ts_node_child_count(bodyNode); ++i) {
            TSNode member = ts_node_child(bodyNode, i);
            std::string mtype = ts_node_type(member);

            if (mtype == "field_declaration") {
                parseField(member, src, cls);
            } else if (mtype == "method_declaration" || mtype == "constructor_declaration") {
                parseMethod(member, src, cls);
            } else if (mtype == "class_declaration" || mtype == "interface_declaration" ||
                       mtype == "enum_declaration" || mtype == "record_declaration") {
                parseClass(member, src, cu, &cls);
            }
        }
    }

    if (parentClass) {
        parentClass->innerClasses.push_back(std::move(cls));
    } else {
        cu.classes.push_back(std::move(cls));
    }
}

void JavaParser::parseField(TSNode node, const std::string& src, JavaClass& cls) {
    JavaField field;
    field.range = getNodeRange(node);

    for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
        TSNode c = ts_node_child(node, i);
        std::string ctype = ts_node_type(c);

        if (ctype == "modifiers") {
            parseModifiersAndAnnotations(c, src, field.annotations);
            for (uint32_t j = 0; j < ts_node_child_count(c); ++j) {
                TSNode m = ts_node_child(c, j);
                std::string mtype = ts_node_type(m);
                if (mtype != "marker_annotation" && mtype != "annotation") {
                    field.modifiers.push_back(getNodeText(m, src));
                }
            }
        } else if (ctype == "type_identifier" || ctype == "generic_type" ||
                   ctype == "integral_type" || ctype == "floating_point_type" ||
                   ctype == "boolean_type" || ctype == "array_type") {
            field.type = parseType(c, src);
        } else if (ctype == "variable_declarator") {
            for (uint32_t j = 0; j < ts_node_child_count(c); ++j) {
                TSNode v = ts_node_child(c, j);
                if (std::string(ts_node_type(v)) == "identifier") {
                    field.name = getNodeText(v, src);
                    field.nameRange = getNodeRange(v);
                    break;
                }
            }
        }
    }

    if (!field.name.empty()) {
        cls.fields.push_back(std::move(field));
    }
}

void JavaParser::parseMethod(TSNode node, const std::string& src, JavaClass& cls) {
    JavaMethod method;
    method.range = getNodeRange(node);
    std::string mtype = ts_node_type(node);
    method.isConstructor = (mtype == "constructor_declaration");

    // Look for doc comment immediately preceding
    TSNode prev = ts_node_prev_sibling(node);
    if (!ts_node_is_null(prev) && std::string(ts_node_type(prev)) == "block_comment") {
        method.docComment = getNodeText(prev, src);
    }

    for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
        TSNode c = ts_node_child(node, i);
        std::string ctype = ts_node_type(c);

        if (ctype == "modifiers") {
            parseModifiersAndAnnotations(c, src, method.annotations);
            for (uint32_t j = 0; j < ts_node_child_count(c); ++j) {
                TSNode m = ts_node_child(c, j);
                std::string mtype2 = ts_node_type(m);
                if (mtype2 != "marker_annotation" && mtype2 != "annotation") {
                    method.modifiers.push_back(getNodeText(m, src));
                }
            }
        } else if (ctype == "type_identifier" || ctype == "generic_type" ||
                   ctype == "integral_type" || ctype == "floating_point_type" ||
                   ctype == "boolean_type" || ctype == "void_type" || ctype == "array_type") {
            if (!method.isConstructor) {
                method.returnType = parseType(c, src);
            }
        } else if (ctype == "identifier") {
            method.name = getNodeText(c, src);
            method.nameRange = getNodeRange(c);
        } else if (ctype == "formal_parameters") {
            for (uint32_t j = 0; j < ts_node_child_count(c); ++j) {
                TSNode param = ts_node_child(c, j);
                if (std::string(ts_node_type(param)) == "formal_parameter" ||
                    std::string(ts_node_type(param)) == "spread_parameter") {
                    JavaTypeRef ptype;
                    std::string pname;
                    Range pNameRange = getNodeRange(param);
                    for (uint32_t k = 0; k < ts_node_child_count(param); ++k) {
                        TSNode pchild = ts_node_child(param, k);
                        std::string pchildType = ts_node_type(pchild);
                        if (pchildType == "type_identifier" || pchildType == "generic_type" ||
                            pchildType == "integral_type" || pchildType == "floating_point_type" ||
                            pchildType == "boolean_type" || pchildType == "array_type") {
                            ptype = parseType(pchild, src);
                        } else if (pchildType == "identifier") {
                            pname = getNodeText(pchild, src);
                            pNameRange = getNodeRange(pchild);
                        }
                    }
                    if (!pname.empty()) {
                        method.parameters.push_back({ptype, pname});
                        method.parameterRanges.push_back(pNameRange);
                    }
                }
            }
        } else if (ctype == "block") {
            method.bodyRange = getNodeRange(c);
            collectLocalVariables(c, src, method.localVariables);
        }
    }

    if (!method.name.empty()) {
        cls.methods.push_back(std::move(method));
    }
}

void JavaParser::collectLocalVariables(TSNode node, const std::string& src, std::vector<JavaVariable>& localVars) const {
    if (ts_node_is_null(node)) return;
    std::string type = ts_node_type(node);
    if (type == "local_variable_declaration") {
        JavaTypeRef varType;
        for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
            TSNode c = ts_node_child(node, i);
            std::string ctype = ts_node_type(c);
            if (ctype == "type_identifier" || ctype == "generic_type" ||
                ctype == "integral_type" || ctype == "floating_point_type" ||
                ctype == "boolean_type" || ctype == "array_type") {
                varType = parseType(c, src);
            } else if (ctype == "variable_declarator") {
                for (uint32_t j = 0; j < ts_node_child_count(c); ++j) {
                    TSNode v = ts_node_child(c, j);
                    if (std::string(ts_node_type(v)) == "identifier") {
                        JavaVariable jv;
                        jv.name = getNodeText(v, src);
                        jv.type = varType;
                        jv.range = getNodeRange(c);
                        jv.nameRange = getNodeRange(v);
                        localVars.push_back(jv);
                        break;
                    }
                }
            }
        }
    } else if (type == "enhanced_for_statement") {
        JavaTypeRef varType;
        std::string varName;
        Range nameRange;
        for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
            TSNode c = ts_node_child(node, i);
            std::string ctype = ts_node_type(c);
            if (ctype == "type_identifier" || ctype == "generic_type" ||
                ctype == "integral_type" || ctype == "floating_point_type" ||
                ctype == "boolean_type" || ctype == "array_type") {
                varType = parseType(c, src);
            } else if (ctype == "identifier" && varName.empty()) {
                varName = getNodeText(c, src);
                nameRange = getNodeRange(c);
            }
        }
        if (!varName.empty()) {
            JavaVariable jv;
            jv.name = varName;
            jv.type = varType;
            jv.range = getNodeRange(node);
            jv.nameRange = nameRange;
            localVars.push_back(jv);
        }
    } else if (type == "catch_formal_parameter") {
        JavaTypeRef varType;
        std::string varName;
        Range nameRange;
        for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
            TSNode c = ts_node_child(node, i);
            std::string ctype = ts_node_type(c);
            if (ctype == "catch_type" || ctype == "type_identifier") {
                varType = parseType(c, src);
            } else if (ctype == "identifier") {
                varName = getNodeText(c, src);
                nameRange = getNodeRange(c);
            }
        }
        if (!varName.empty()) {
            JavaVariable jv;
            jv.name = varName;
            jv.type = varType;
            jv.range = getNodeRange(node);
            jv.nameRange = nameRange;
            localVars.push_back(jv);
        }
    }

    for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
        collectLocalVariables(ts_node_child(node, i), src, localVars);
    }
}

} // namespace javalsp
