#pragma once

#include "parser/java_ast.h"
#include <string>
#include <vector>

namespace javalsp {

struct TypeTransitionEdge {
    std::string fromTypePattern; // e.g. "List<A>", "Stream<A>", "A", "Optional<A>"
    std::string toTypePattern;   // e.g. "Stream<A>", "Stream<B>", "List<B>", "String"
    std::string callCode;        // e.g. "stream()", "map($1-it->$2)", "toList()"
    std::string label;           // display label
    int cost{1};
};

struct ChainedCandidate {
    std::string label;
    std::string insertText;
    int chainLength{1};
    std::string returnType;
    std::string detail;
};

class TypeGraphSearch {
public:
    TypeGraphSearch();

    std::vector<ChainedCandidate> searchPaths(
        const JavaTypeRef& sourceType,
        const JavaTypeRef& targetType);

private:
    std::vector<TypeTransitionEdge> edges_;
    void initKnowledgeGraph();
};

} // namespace javalsp
