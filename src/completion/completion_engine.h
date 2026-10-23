#pragma once

#include "lsp/protocol.h"
#include "parser/java_ast.h"
#include "semantics/scope.h"
#include "semantics/symbol_table.h"
#include "completion/graph_search.h"
#include <string>
#include <vector>
#include <memory>

namespace javalsp {

class CompletionEngine {
public:
    CompletionEngine(const SymbolTable& symTab);

    CompletionList complete(
        const JavaCompilationUnit& cu,
        const std::string& src,
        const Position& pos,
        std::shared_ptr<Scope> fileScope);

private:
    const SymbolTable& symTab_;
    TypeGraphSearch graphSearch_;

    void addClassMembers(
        const JavaClass& cls,
        const JavaTypeRef& instanceType,
        const JavaTypeRef& expectedType,
        bool isStaticOnly,
        std::vector<CompletionItem>& outItems);

    std::pair<std::string, std::string> extractTriggerAndPrefix(
        const std::string& line,
        int character) const;

    JavaTypeRef extractExpectedType(
        const std::string& line,
        const JavaCompilationUnit& cu) const;
};

} // namespace javalsp
