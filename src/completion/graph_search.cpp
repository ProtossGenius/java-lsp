#include "completion/graph_search.h"
#include <queue>
#include <set>
#include <algorithm>

namespace javalsp {

TypeGraphSearch::TypeGraphSearch() {
    initKnowledgeGraph();
}

void TypeGraphSearch::initKnowledgeGraph() {
    edges_ = {
        // List<A> -> Stream<A>
        {"List", "Stream", "stream()", "stream()", 1},
        {"Set", "Stream", "stream()", "stream()", 1},
        {"Collection", "Stream", "stream()", "stream()", 1},

        // Stream<A> -> Stream<B>
        {"Stream", "Stream", "map($1-it->$2)", "map(it -> ...)", 1},
        {"Stream", "Stream_String", "map(Object::toString)", "map(Object::toString)", 1},
        {"Stream", "Stream_String", "map(String::valueOf)", "map(String::valueOf)", 1},

        // Stream<B> -> List<B>
        {"Stream", "List", "toList()", "toList()", 1},
        {"Stream", "List", "collect(Collectors.toList())", "collect(Collectors.toList())", 1},
        {"Stream", "Set", "collect(Collectors.toSet())", "collect(Collectors.toSet())", 1},

        // Stream_String -> List<String>
        {"Stream_String", "List", "toList()", "toList()", 1},
        {"Stream_String", "List", "collect(Collectors.toList())", "collect(Collectors.toList())", 1},

        // A -> String
        {"Any", "String", "toString()", "toString()", 1},

        // Optional<A> -> Optional<B>
        {"Optional", "Optional", "map($1-it->$2)", "map(it -> ...)", 1},
        {"Optional", "Any", "orElse(null)", "orElse(null)", 1},
    };
}

struct SearchNode {
    std::string currentKind; // "List", "Stream", "Stream_String", "String", etc.
    std::string currentArg;  // "Integer", "String", etc.
    std::vector<std::string> calls;
    std::vector<std::string> labels;
    int length{0};
};

std::vector<ChainedCandidate> TypeGraphSearch::searchPaths(
    const JavaTypeRef& sourceType,
    const JavaTypeRef& targetType) {

    std::vector<ChainedCandidate> results;
    if (sourceType.name.empty() || targetType.name.empty()) {
        return results;
    }

    std::string targetKind = targetType.name;
    std::string targetArg = targetType.typeArguments.empty() ? "" : targetType.typeArguments[0].name;

    std::queue<SearchNode> q;
    SearchNode start;
    start.currentKind = sourceType.name;
    start.currentArg = sourceType.typeArguments.empty() ? "" : sourceType.typeArguments[0].name;
    start.length = 0;
    q.push(start);

    std::set<std::string> visited;

    while (!q.empty()) {
        auto curr = q.front();
        q.pop();

        if (curr.length > 4) continue; // Limit max chain depth

        // Check if goal reached
        if (curr.length > 0 && curr.currentKind == targetKind) {
            bool argMatch = false;
            if (targetArg.empty() || curr.currentArg == targetArg) {
                argMatch = true;
            }
            if (argMatch) {
                std::string fullCall;
                std::string fullLabel;
                for (size_t i = 0; i < curr.calls.size(); ++i) {
                    if (i > 0) {
                        fullCall += ".";
                        fullLabel += ".";
                    }
                    fullCall += curr.calls[i];
                    fullLabel += curr.labels[i];
                }

                ChainedCandidate cand;
                cand.insertText = fullCall;
                cand.label = fullCall;
                cand.chainLength = curr.length;
                cand.returnType = targetType.toString();
                cand.detail = targetType.toString() + " (Chained completion)";
                results.push_back(cand);
                continue;
            }
        }

        std::string stateKey = curr.currentKind + "<" + curr.currentArg + ">:" + std::to_string(curr.length);
        if (visited.count(stateKey)) continue;
        visited.insert(stateKey);

        for (const auto& edge : edges_) {
            if (edge.fromTypePattern == curr.currentKind || edge.fromTypePattern == "Any") {
                SearchNode next = curr;
                next.length = curr.length + edge.cost;
                next.calls.push_back(edge.callCode);
                next.labels.push_back(edge.label);

                if (edge.toTypePattern == "Stream_String") {
                    next.currentKind = "Stream";
                    next.currentArg = "String";
                } else if (edge.toTypePattern == "Stream" && edge.callCode.find("map") != std::string::npos) {
                    next.currentKind = "Stream";
                    next.currentArg = targetArg.empty() ? "Object" : targetArg;
                } else if (edge.toTypePattern == "String") {
                    next.currentKind = "String";
                    next.currentArg = "";
                } else {
                    next.currentKind = edge.toTypePattern;
                    // Maintain current generic argument
                }

                q.push(next);
            }
        }
    }

    // Sort by chain length ascending, then alphabetically by label
    std::sort(results.begin(), results.end(), [](const ChainedCandidate& a, const ChainedCandidate& b) {
        if (a.chainLength != b.chainLength) {
            return a.chainLength < b.chainLength;
        }
        return a.label < b.label;
    });

    return results;
}

} // namespace javalsp
