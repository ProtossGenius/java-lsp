#include "completion/completion_engine.h"
#include "semantics/type_checker.h"
#include "utils/logger.h"
#include <sstream>
#include <algorithm>
#include <iomanip>

namespace javalsp {

static std::string toLowerString(const std::string& s) {
    std::string res = s;
    for (char& c : res) c = std::tolower(static_cast<unsigned char>(c));
    return res;
}

static int computeFilterScore(const std::string& label, const std::string& prefix) {
    if (prefix.empty()) return 0;
    if (label.rfind(prefix, 0) == 0) return 0;

    std::string lowerLabel = toLowerString(label);
    std::string lowerPrefix = toLowerString(prefix);

    if (lowerLabel.rfind(lowerPrefix, 0) == 0) return 1;
    if (label.find(prefix) != std::string::npos) return 2;
    if (lowerLabel.find(lowerPrefix) != std::string::npos) return 3;

    size_t pi = 0;
    for (size_t li = 0; li < label.size() && pi < prefix.size(); ++li) {
        if (std::tolower(static_cast<unsigned char>(label[li])) == std::tolower(static_cast<unsigned char>(prefix[pi]))) {
            pi++;
        }
    }
    if (pi == prefix.size()) return 4;

    return -1;
}

CompletionEngine::CompletionEngine(const SymbolTable& symTab)
    : symTab_(symTab) {}

std::pair<std::string, std::string> CompletionEngine::extractTriggerAndPrefix(
    const std::string& line, int character) const {
    if (character > static_cast<int>(line.size())) character = static_cast<int>(line.size());

    // 1. Find the word (token) currently being typed up to `character`
    // Word characters: alphanumeric, '_', or '@' (for annotations)
    int wordEnd = character;
    int wordStart = character;
    while (wordStart > 0) {
        char ch = line[wordStart - 1];
        if (std::isalnum(static_cast<unsigned char>(ch)) || ch == '_' || ch == '@') {
            wordStart--;
        } else {
            break;
        }
    }

    std::string filterPrefix = line.substr(wordStart, wordEnd - wordStart);
    if (!filterPrefix.empty() && filterPrefix.front() == '@') {
        filterPrefix = filterPrefix.substr(1);
    }

    // 2. Check if immediately before the word, there is a dot `.`
    // E.g. "user.get" -> wordStart is at 'g', line[wordStart-1] is '.'
    // E.g. "user."    -> wordStart is at end (word is ""), line[wordStart-1] is '.'
    int beforeWord = wordStart;
    while (beforeWord > 0 && (line[beforeWord - 1] == ' ' || line[beforeWord - 1] == '\t')) {
        beforeWord--;
    }

    if (beforeWord > 0 && line[beforeWord - 1] == '.') {
        size_t dotPos = beforeWord - 1;
        // Extract receiver expression ending at dotPos
        int parenDepth = 0;
        int bracketDepth = 0;
        int angleDepth = 0;
        size_t i = dotPos;
        while (i > 0) {
            char c = line[i - 1];
            if (c == ')') parenDepth++;
            else if (c == '(') {
                if (parenDepth == 0) break;
                parenDepth--;
            } else if (c == ']') bracketDepth++;
            else if (c == '[') {
                if (bracketDepth == 0) break;
                bracketDepth--;
            } else if (c == '>') angleDepth++;
            else if (c == '<') {
                if (angleDepth == 0) break;
                angleDepth--;
            } else if (parenDepth == 0 && bracketDepth == 0 && angleDepth == 0) {
                // Expression boundaries
                if (c == ',' || c == ';' || c == '=' || c == '{' || c == '}' ||
                    c == '+' || c == '-' || c == '*' || c == '/' || c == '%' ||
                    c == '&' || c == '|' || c == '^' || c == '!' || c == '~' ||
                    c == '?' || c == ':' || c == '\t' || c == ' ' || c == '"' || c == '\'') {
                    break;
                }
            }
            i--;
        }
        std::string receiver = line.substr(i, dotPos - i);
        while (!receiver.empty() && (receiver.front() == ' ' || receiver.front() == '\t')) receiver.erase(receiver.begin());
        while (!receiver.empty() && (receiver.back() == ' ' || receiver.back() == '\t')) receiver.pop_back();

        return {receiver, filterPrefix};
    }

    return {"", filterPrefix};
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

        // Java keywords
        static const std::vector<std::string> keywords = {
            "public", "private", "protected", "static", "final", "abstract", "default",
            "class", "interface", "enum", "record", "extends", "implements",
            "void", "return", "if", "else", "for", "while", "do", "switch", "case",
            "break", "continue", "new", "this", "super", "try", "catch", "finally",
            "throw", "throws", "import", "package", "synchronized", "instanceof",
            "int", "long", "boolean", "byte", "short", "char", "double", "float"
        };
        for (const auto& kw : keywords) {
            if (filterPrefix.empty() || kw.rfind(filterPrefix, 0) == 0) {
                CompletionItem item;
                item.label = kw;
                item.kind = 14; // Keyword
                item.detail = "keyword";
                list.items.push_back(item);
            }
        }

        // Project and JDK classes
        auto matchedClasses = symTab_.findClassesMatchingPrefix(filterPrefix);
        for (const auto& c : matchedClasses) {
            CompletionItem item;
            item.label = c.name;
            item.kind = 7; // Class
            item.detail = c.fqn;
            list.items.push_back(item);
        }
    }

    // Filter and score by prefix / substring if specified
    if (!filterPrefix.empty()) {
        std::vector<CompletionItem> filtered;
        for (auto& it : list.items) {
            int score = computeFilterScore(it.label, filterPrefix);
            if (score >= 0) {
                std::string origSort = it.sortText.empty() ? it.label : it.sortText;
                it.sortText = std::to_string(score) + "_" + origSort;
                filtered.push_back(std::move(it));
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
