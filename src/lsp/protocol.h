#pragma once

#include <string>
#include <vector>
#include <map>
#include <optional>
#include <llvm/Support/JSON.h>

namespace javalsp {

struct Position {
    int line{0};
    int character{0};

    bool operator==(const Position& o) const {
        return line == o.line && character == o.character;
    }
    bool operator<(const Position& o) const {
        if (line != o.line) return line < o.line;
        return character < o.character;
    }
    bool operator<=(const Position& o) const {
        return *this < o || *this == o;
    }
};

struct Range {
    Position start;
    Position end;

    bool contains(const Position& pos) const {
        return (start <= pos) && (pos <= end);
    }
    bool operator==(const Range& o) const {
        return start == o.start && end == o.end;
    }
};

struct Location {
    std::string uri;
    Range range;
};

struct Diagnostic {
    Range range;
    int severity{1}; // 1 = Error, 2 = Warning, 3 = Info, 4 = Hint
    std::string code;
    std::string source{"java-lsp"};
    std::string message;
};

struct TextEdit {
    Range range;
    std::string newText;
};

struct CompletionItem {
    std::string label;
    int kind{1}; // Method = 2, Field = 5, Variable = 6, Class = 7, etc.
    std::string detail;
    std::string documentation;
    std::string sortText;
    std::string filterText;
    std::string insertText;
    int insertTextFormat{1}; // 1 = PlainText, 2 = Snippet
    std::optional<TextEdit> textEdit;
    std::vector<TextEdit> additionalTextEdits;
};

struct CompletionList {
    bool isIncomplete{false};
    std::vector<CompletionItem> items;
};

struct Hover {
    std::string contents; // markdown
    std::optional<Range> range;
};

struct ParameterInformation {
    std::string label;
    std::string documentation;
};

struct SignatureInformation {
    std::string label;
    std::string documentation;
    std::vector<ParameterInformation> parameters;
};

struct SignatureHelp {
    std::vector<SignatureInformation> signatures;
    int activeSignature{0};
    int activeParameter{0};
};

struct DocumentSymbol {
    std::string name;
    std::string detail;
    int kind{0};
    Range range;
    Range selectionRange;
    std::vector<DocumentSymbol> children;
};

struct WorkspaceEdit {
    std::map<std::string, std::vector<TextEdit>> changes;
};

struct OverridableMethodItem {
    bool required{false};
    bool unimplemented{false};
    std::string name;
    std::string signature;
    std::string returnType;
    std::string parent;
};

// JSON serialization helpers
llvm::json::Value toJson(const Position& p);
llvm::json::Value toJson(const Range& r);
llvm::json::Value toJson(const Location& loc);
llvm::json::Value toJson(const Diagnostic& d);
llvm::json::Value toJson(const TextEdit& te);
llvm::json::Value toJson(const CompletionItem& ci);
llvm::json::Value toJson(const CompletionList& cl);
llvm::json::Value toJson(const Hover& h);
llvm::json::Value toJson(const SignatureHelp& sh);
llvm::json::Value toJson(const DocumentSymbol& ds);
llvm::json::Value toJson(const WorkspaceEdit& we);
llvm::json::Value toJson(const OverridableMethodItem& item);

bool parsePosition(const llvm::json::Value& v, Position& out);
bool parseRange(const llvm::json::Value& v, Range& out);
bool parseLocation(const llvm::json::Value& v, Location& out);

} // namespace javalsp
