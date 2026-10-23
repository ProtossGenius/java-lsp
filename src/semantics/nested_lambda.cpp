#include "semantics/nested_lambda.h"
#include "utils/logger.h"

namespace javalsp {

std::string NestedLambdaAnalyzer::getNodeText(TSNode node, const std::string& src) {
    if (ts_node_is_null(node)) return "";
    uint32_t start = ts_node_start_byte(node);
    uint32_t end = ts_node_end_byte(node);
    if (start >= src.size() || end > src.size() || start >= end) return "";
    return src.substr(start, end - start);
}

Range NestedLambdaAnalyzer::getNodeRange(TSNode node) {
    if (ts_node_is_null(node)) return Range{{0, 0}, {0, 0}};
    TSPoint s = ts_node_start_point(node);
    TSPoint e = ts_node_end_point(node);
    return Range{
        {static_cast<int>(s.row), static_cast<int>(s.column)},
        {static_cast<int>(e.row), static_cast<int>(e.column)}
    };
}

JavaTypeRef NestedLambdaAnalyzer::inferLambdaParamType(
    TSNode lambdaNode,
    const std::string& src,
    std::shared_ptr<Scope> currentScope) {

    // Look at parent: argument_list -> method_invocation
    TSNode parent = ts_node_parent(lambdaNode);
    if (ts_node_is_null(parent)) return JavaTypeRef{"Object", "java.lang.Object"};

    if (std::string(ts_node_type(parent)) == "argument_list") {
        TSNode callNode = ts_node_parent(parent);
        if (!ts_node_is_null(callNode) && std::string(ts_node_type(callNode)) == "method_invocation") {
            // Find receiver object
            TSNode receiver = ts_node_child_by_field_name(callNode, "object", 6);
            if (ts_node_is_null(receiver)) {
                receiver = ts_node_child(callNode, 0);
            }
            std::string receiverText = getNodeText(receiver, src);

            // If receiverText ends with .stream(), strip it or resolve stream element
            std::string baseVar = receiverText;
            size_t dotPos = baseVar.find('.');
            if (dotPos != std::string::npos) {
                baseVar = baseVar.substr(0, dotPos);
            }

            // Lookup baseVar in scope
            auto var = currentScope->lookupVariable(baseVar);
            if (var) {
                // If baseVar is List<T> or Set<T> or Collection<T> or Stream<T>
                if (!var->type.typeArguments.empty()) {
                    return var->type.typeArguments[0];
                }
            }
        }
    }

    return JavaTypeRef{"Object", "java.lang.Object"};
}

void NestedLambdaAnalyzer::analyzeMethodLambdas(
    TSNode methodNode,
    const std::string& src,
    std::shared_ptr<Scope> methodScope,
    const std::vector<JavaField>& classFields) {

    // First add class fields to scope
    for (const auto& f : classFields) {
        JavaVariable var;
        var.name = f.name;
        var.type = f.type;
        var.range = f.range;
        var.nameRange = f.nameRange;
        methodScope->addVariable(var);
    }

    walkNodeForLambdas(methodNode, src, methodScope, nullptr);
}

void NestedLambdaAnalyzer::walkNodeForLambdas(
    TSNode node,
    const std::string& src,
    std::shared_ptr<Scope> currentScope,
    std::shared_ptr<JavaLambda> currentLambda) {

    if (ts_node_is_null(node)) return;

    std::string type = ts_node_type(node);

    // Track local variable declarations
    if (type == "local_variable_declaration") {
        JavaTypeRef varType;
        for (uint32_t i = 0; i < ts_node_child_count(node); ++i) {
            TSNode c = ts_node_child(node, i);
            std::string ctype = ts_node_type(c);
            if (ctype == "generic_type" || ctype == "type_identifier" ||
                ctype == "integral_type" || ctype == "boolean_type" || ctype == "array_type") {
                // parse type
                if (ctype == "generic_type") {
                    TSNode base = ts_node_child(c, 0);
                    varType.name = getNodeText(base, src);
                    for (uint32_t j = 0; j < ts_node_child_count(c); ++j) {
                        TSNode argList = ts_node_child(c, j);
                        if (std::string(ts_node_type(argList)) == "type_arguments") {
                            for (uint32_t k = 0; k < ts_node_child_count(argList); ++k) {
                                TSNode arg = ts_node_child(argList, k);
                                std::string atype = ts_node_type(arg);
                                if (atype == "type_identifier" || atype == "generic_type") {
                                    varType.typeArguments.push_back(JavaTypeRef{getNodeText(arg, src), ""});
                                }
                            }
                        }
                    }
                } else {
                    varType.name = getNodeText(c, src);
                }
            } else if (ctype == "variable_declarator") {
                for (uint32_t j = 0; j < ts_node_child_count(c); ++j) {
                    TSNode v = ts_node_child(c, j);
                    if (std::string(ts_node_type(v)) == "identifier") {
                        JavaVariable jv;
                        jv.name = getNodeText(v, src);
                        jv.type = varType;
                        jv.range = getNodeRange(c);
                        jv.nameRange = getNodeRange(v);
                        currentScope->addVariable(jv);
                        if (currentLambda) {
                            currentLambda->localVariables.push_back(jv);
                        }
                        break;
                    }
                }
            }
        }
    } else if (type == "lambda_expression") {
        auto lambda = std::make_shared<JavaLambda>();
        lambda->range = getNodeRange(node);
        lambda->parentLambda = currentLambda;

        auto lambdaScope = Scope::create(ScopeKind::LAMBDA, currentScope, lambda->range);

        // Infer or extract parameter types
        JavaTypeRef inferredType = inferLambdaParamType(node, src, currentScope);

        TSNode paramsNode = ts_node_child_by_field_name(node, "parameters", 10);
        if (ts_node_is_null(paramsNode)) {
            // Identifier as single parameter: x -> ...
            TSNode firstChild = ts_node_child(node, 0);
            if (std::string(ts_node_type(firstChild)) == "identifier") {
                std::string pname = getNodeText(firstChild, src);
                lambda->parameters.push_back({inferredType, pname});
                lambda->parameterRanges.push_back(getNodeRange(firstChild));

                JavaVariable pvar;
                pvar.name = pname;
                pvar.type = inferredType;
                pvar.range = getNodeRange(firstChild);
                pvar.nameRange = pvar.range;
                lambdaScope->addVariable(pvar);
            }
        } else {
            // formal_parameters: (x, y) -> ... or (Type x) -> ...
            for (uint32_t i = 0; i < ts_node_child_count(paramsNode); ++i) {
                TSNode p = ts_node_child(paramsNode, i);
                std::string ptype = ts_node_type(p);
                if (ptype == "formal_parameter") {
                    // Typed parameter
                    JavaTypeRef explicitType;
                    std::string pname;
                    for (uint32_t j = 0; j < ts_node_child_count(p); ++j) {
                        TSNode pc = ts_node_child(p, j);
                        std::string pctype = ts_node_type(pc);
                        if (pctype == "type_identifier" || pctype == "generic_type") {
                            explicitType.name = getNodeText(pc, src);
                        } else if (pctype == "identifier") {
                            pname = getNodeText(pc, src);
                        }
                    }
                    if (explicitType.name.empty()) explicitType = inferredType;
                    lambda->parameters.push_back({explicitType, pname});
                    lambda->parameterRanges.push_back(getNodeRange(p));

                    JavaVariable pvar;
                    pvar.name = pname;
                    pvar.type = explicitType;
                    pvar.range = getNodeRange(p);
                    pvar.nameRange = pvar.range;
                    lambdaScope->addVariable(pvar);
                } else if (ptype == "inferred_parameters" || ptype == "identifier") {
                    std::string pname = getNodeText(p, src);
                    lambda->parameters.push_back({inferredType, pname});
                    lambda->parameterRanges.push_back(getNodeRange(p));

                    JavaVariable pvar;
                    pvar.name = pname;
                    pvar.type = inferredType;
                    pvar.range = getNodeRange(p);
                    pvar.nameRange = pvar.range;
                    lambdaScope->addVariable(pvar);
                }
            }
        }

        TSNode bodyNode = ts_node_child_by_field_name(node, "body", 4);
        if (!ts_node_is_null(bodyNode)) {
            lambda->bodyRange = getNodeRange(bodyNode);
            // Walk inside lambda body with lambdaScope and this lambda as parent
            walkNodeForLambdas(bodyNode, src, lambdaScope, lambda);
        }

        if (currentLambda) {
            currentLambda->nestedLambdas.push_back(lambda);
        }
        currentScope->addLambda(lambda);
        return;
    }

    uint32_t count = ts_node_child_count(node);
    for (uint32_t i = 0; i < count; ++i) {
        walkNodeForLambdas(ts_node_child(node, i), src, currentScope, currentLambda);
    }
}

} // namespace javalsp
