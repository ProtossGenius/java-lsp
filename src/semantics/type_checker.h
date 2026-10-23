#pragma once

#include "parser/java_ast.h"
#include "semantics/scope.h"
#include "semantics/symbol_table.h"
#include <tree_sitter/api.h>
#include <string>
#include <vector>
#include <memory>

namespace javalsp {

class TypeChecker {
public:
    static std::vector<Diagnostic> check(
        const JavaCompilationUnit& cu,
        const std::string& src,
        TSNode rootNode,
        const SymbolTable& symTab);

    static JavaTypeRef resolveExpressionType(
        const std::string& expr,
        std::shared_ptr<Scope> scope,
        const JavaCompilationUnit& cu,
        const SymbolTable& symTab);

private:
    static void collectDiagnostics(
        TSNode node,
        const std::string& src,
        const JavaCompilationUnit& cu,
        const SymbolTable& symTab,
        std::shared_ptr<Scope> currentScope,
        std::vector<Diagnostic>& diags);

    static std::string getNodeText(TSNode node, const std::string& src);
    static Range getNodeRange(TSNode node);
};

} // namespace javalsp
