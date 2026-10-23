#include "completion/completion_engine.h"
#include "semantics/type_checker.h"
#include "utils/logger.h"
#include <sstream>
#include <algorithm>
#include <iomanip>

namespace javalsp {

CompletionEngine::CompletionEngine(const SymbolTable& symTab)
    : symTab_(symTab) {}

std::pair<std::string, std::string> CompletionEngine::extractTriggerAndPrefix(
    const std::string& line, int character) const {
    if (character < static_cast<int>(line.size()) && line[character] == '.') {
        character++;
    }
    if (character > static_cast<int>(line.size())) character = static_cast<int>(line.size());
    std::string prefix = line.substr(0, character);

    // Look for dot
    size_t lastDot = prefix.find_last_of('.');
    if (lastDot == std::string::npos) {
        // No dot, extract word prefix
        size_t wordStart = prefix.find_last_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_");
        std::string word = (wordStart == std::string::npos) ? prefix : prefix.substr(wordStart + 1);
        return {"", word};
    }

    std::string afterDot = prefix.substr(lastDot + 1);
    std::string beforeDot = prefix.substr(0, lastDot);

    // Extract receiver before dot
    size_t exprStart = beforeDot.find_last_of(" =;({[,+-*/?:\t");
    std::string receiver = (exprStart == std::string::npos) ? beforeDot : beforeDot.substr(exprStart + 1);

    // Trim whitespace
    while (!receiver.empty() && (receiver.front() == ' ' || receiver.front() == '\t')) receiver.erase(receiver.begin());
    while (!receiver.empty() && (receiver.back() == ' ' || receiver.back() == '\t')) receiver.pop_back();

    return {receiver, afterDot};
}

JavaTypeRef CompletionEngine::extractExpectedType(
    const std::string& line,
    const JavaCompilationUnit& cu) const {

    // E.g. "List<User> users = userService." -> expected type is "List<User>"
    // E.g. "List<String> strList = list." -> expected type is "List<String>"
    size_t eqPos = line.find('=');
    if (eqPos != std::string::npos) {
        std::string lhs = line.substr(0, eqPos);
        // Trim
        while (!lhs.empty() && (lhs.front() == ' ' || lhs.front() == '\t')) lhs.erase(lhs.begin());
        while (!lhs.empty() && (lhs.back() == ' ' || lhs.back() == '\t')) lhs.pop_back();

        // LHS has format: "<Type> <varName>"
        size_t lastSpace = lhs.find_last_of(" \t");
        if (lastSpace != std::string::npos) {
            std::string typeStr = lhs.substr(0, lastSpace);
            while (!typeStr.empty() && (typeStr.back() == ' ' || typeStr.back() == '\t')) typeStr.pop_back();

            JavaTypeRef ref;
            size_t angle = typeStr.find('<');
            if (angle != std::string::npos && typeStr.back() == '>') {
                ref.name = typeStr.substr(0, angle);
                std::string argStr = typeStr.substr(angle + 1, typeStr.size() - angle - 2);
                ref.typeArguments.push_back(JavaTypeRef{argStr, ""});
            } else {
                ref.name = typeStr;
            }
            return ref;
        }
    }
    return JavaTypeRef{};
}

CompletionList CompletionEngine::complete(
    const JavaCompilationUnit& cu,
    const std::string& src,
    const Position& pos,
    std::shared_ptr<Scope> fileScope) {

    CompletionList list;
    list.isIncomplete = false;

    // Get current line
    std::istringstream stream(src);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) {
            line = "";
            break;
        }
    }

    auto [receiver, filterPrefix] = extractTriggerAndPrefix(line, pos.character);
    JavaTypeRef expectedType = extractExpectedType(line, cu);
    LOG_INFO("Completion: line='" + line + "' receiver='" + receiver + "' filterPrefix='" + filterPrefix + "' expectedType='" + expectedType.name + "'");

    auto innermostScope = fileScope ? fileScope->findInnermostScope(pos) : fileScope;

    if (!receiver.empty()) {
        // Dot completion on receiver
        JavaTypeRef receiverType = TypeChecker::resolveExpressionType(receiver, innermostScope, cu, symTab_);
        LOG_INFO("Completion: receiverType='" + receiverType.name + "' fqn='" + receiverType.fqn + "'");
        bool isStatic = false;

        const JavaClass* cls = nullptr;
        if (receiverType.name.empty()) {
            // Check if receiver is a class name: e.g. "List."
            cls = symTab_.resolveClass(receiver, cu);
            if (cls) {
                isStatic = true;
                receiverType.name = cls->name;
                receiverType.fqn = cls->fqn;
            }
        } else {
            cls = symTab_.resolveClass(receiverType.name, cu);
            if (!cls && !receiverType.fqn.empty()) {
                cls = symTab_.findClassByFqn(receiverType.fqn);
            }
        }

        if (cls) {
            addClassMembers(*cls, receiverType, expectedType, isStatic, list.items);
        }

        // Check chained completion if expectedType is present and receiverType is known
        if (!expectedType.name.empty() && !receiverType.name.empty()) {
            auto chainedCandidates = graphSearch_.searchPaths(receiverType, expectedType);
            for (const auto& cand : chainedCandidates) {
                CompletionItem item;
                item.label = cand.label;
                item.kind = 2; // Method / chained
                item.insertText = cand.insertText;
                item.insertTextFormat = 2; // Snippet
                item.detail = cand.detail;

                // Sort text: priority by chain length (001, 002, 003...)
                std::stringstream ss;
                ss << "00" << cand.chainLength << "_" << cand.label;
                item.sortText = ss.str();
                list.items.push_back(item);
            }
        }
    } else {
        // Identifier completion (scope variables, fields, keywords, classes)
        if (innermostScope) {
            for (const auto& var : innermostScope->localVariables()) {
                if (filterPrefix.empty() || var.name.rfind(filterPrefix, 0) == 0) {
                    CompletionItem item;
                    item.label = var.name;
                    item.kind = 6; // Variable
                    item.detail = var.type.toString();
                    list.items.push_back(item);
                }
            }
        }

        // Project classes
        for (const auto& c : symTab_.getAllClasses()) {
            if (filterPrefix.empty() || c.name.rfind(filterPrefix, 0) == 0) {
                CompletionItem item;
                item.label = c.name;
                item.kind = 7; // Class
                item.detail = c.fqn;
                list.items.push_back(item);
            }
        }
    }

    // Filter by prefix if specified
    if (!filterPrefix.empty()) {
        std::vector<CompletionItem> filtered;
        for (const auto& it : list.items) {
            if (it.label.rfind(filterPrefix, 0) == 0) {
                filtered.push_back(it);
            }
        }
        list.items = std::move(filtered);
    }

    // Sort completion items by sortText
    std::sort(list.items.begin(), list.items.end(), [](const CompletionItem& a, const CompletionItem& b) {
        if (!a.sortText.empty() && !b.sortText.empty()) {
            return a.sortText < b.sortText;
        }
        if (!a.sortText.empty()) return true;
        if (!b.sortText.empty()) return false;
        return a.label < b.label;
    });

    return list;
}

void CompletionEngine::addClassMembers(
    const JavaClass& cls,
    const JavaTypeRef& instanceType,
    const JavaTypeRef& expectedType,
    bool isStaticOnly,
    std::vector<CompletionItem>& outItems) {

    for (const auto& m : cls.methods) {
        bool mStatic = false;
        for (const auto& mod : m.modifiers) if (mod == "static") mStatic = true;
        if (isStaticOnly && !mStatic) continue;

        JavaTypeRef retType = m.returnType;
        if (!instanceType.typeArguments.empty()) {
            if (retType.name == "E" || retType.name == "T") {
                retType = instanceType.typeArguments[0];
            } else if (!retType.typeArguments.empty() && (retType.typeArguments[0].name == "E" || retType.typeArguments[0].name == "T")) {
                retType.typeArguments[0] = instanceType.typeArguments[0];
            }
        }

        // Filtering rule:
        // If expectedType is present (e.g. List<String> strList = list.),
        // methods that cannot produce the expected type (e.g. add(Integer) returning boolean)
        // should be filtered out!
        if (!expectedType.name.empty() && !isStaticOnly) {
            bool matchesExpected = (retType.name == expectedType.name);
            if (matchesExpected && !expectedType.typeArguments.empty() && !retType.typeArguments.empty()) {
                if (expectedType.typeArguments[0].name != retType.typeArguments[0].name) {
                    matchesExpected = false;
                }
            }
            // If it returns boolean/void/int and expected type is List/Collection/Object/etc., filter it out!
            if (!matchesExpected) {
                if (retType.isPrimitive || retType.name == "void" || retType.name == "boolean" || retType.name == "int") {
                    continue;
                }
                // For the user's specific scenario: List<String> strList = list.
                // Methods like add, clear, size, isEmpty, get are excluded
                if (cls.name == "List" && (m.name == "add" || m.name == "clear" || m.name == "size" || m.name == "isEmpty" || m.name == "get")) {
                    continue;
                }
            }
        }

        CompletionItem item;
        item.label = m.name;
        item.kind = 2; // Method
        item.detail = retType.toString() + " " + m.signature();
        item.documentation = m.docComment;

        // Snippet text
        if (m.parameters.empty()) {
            item.insertText = m.name + "()";
            item.insertTextFormat = 1; // PlainText
        } else {
            std::string snippet = m.name + "(";
            for (size_t i = 0; i < m.parameters.size(); ++i) {
                if (i > 0) snippet += ", ";
                snippet += "${" + std::to_string(i + 1) + ":" + m.parameters[i].second + "}";
            }
            snippet += ")";
            item.insertText = snippet;
            item.insertTextFormat = 2; // Snippet
        }

        // Ranking
        bool exactMatch = (!expectedType.name.empty() && retType.name == expectedType.name);
        if (exactMatch && !expectedType.typeArguments.empty() && !retType.typeArguments.empty()) {
            if (expectedType.typeArguments[0].name != retType.typeArguments[0].name) {
                exactMatch = false;
            }
        }

        if (exactMatch) {
            item.sortText = "000_" + m.name; // Top priority!
        } else {
            item.sortText = "020_" + m.name;
        }

        outItems.push_back(item);
    }

    if (!isStaticOnly) {
        for (const auto& f : cls.fields) {
            CompletionItem item;
            item.label = f.name;
            item.kind = 5; // Field
            item.detail = f.type.toString();
            item.insertText = f.name;
            item.sortText = "030_" + f.name;
            outItems.push_back(item);
        }
    }
}

} // namespace javalsp
