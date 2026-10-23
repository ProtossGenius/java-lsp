#pragma once

#include "parser/java_ast.h"
#include "semantics/scope.h"
#include <tree_sitter/api.h>
#include <memory>

namespace javalsp {

class NestedLambdaAnalyzer {
public:
    static void analyzeMethodLambdas(
        TSNode methodNode,
        const std::string& src,
        std::shared_ptr<Scope> methodScope,
        const std::vector<JavaField>& classFields);

private:
    static void walkNodeForLambdas(
        TSNode node,
        const std::string& src,
        std::shared_ptr<Scope> currentScope,
        std::shared_ptr<JavaLambda> currentLambda);

    static JavaTypeRef inferLambdaParamType(
        TSNode lambdaNode,
        const std::string& src,
        std::shared_ptr<Scope> currentScope);

    static std::string getNodeText(TSNode node, const std::string& src);
    static Range getNodeRange(TSNode node);
};

} // namespace javalsp
