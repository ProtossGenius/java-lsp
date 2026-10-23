#include "lsp/protocol.h"

namespace javalsp {

llvm::json::Value toJson(const Position& p) {
    return llvm::json::Object{
        {"line", p.line},
        {"character", p.character}
    };
}

llvm::json::Value toJson(const Range& r) {
    return llvm::json::Object{
        {"start", toJson(r.start)},
        {"end", toJson(r.end)}
    };
}

llvm::json::Value toJson(const Location& loc) {
    return llvm::json::Object{
        {"uri", loc.uri},
        {"range", toJson(loc.range)}
    };
}

llvm::json::Value toJson(const Diagnostic& d) {
    llvm::json::Object obj{
        {"range", toJson(d.range)},
        {"severity", d.severity},
        {"source", d.source},
        {"message", d.message}
    };
    if (!d.code.empty()) {
        obj["code"] = d.code;
    }
    return llvm::json::Value(std::move(obj));
}

llvm::json::Value toJson(const TextEdit& te) {
    return llvm::json::Object{
        {"range", toJson(te.range)},
        {"newText", te.newText}
    };
}

llvm::json::Value toJson(const CompletionItem& ci) {
    llvm::json::Object obj{
        {"label", ci.label},
        {"kind", ci.kind}
    };
    if (!ci.detail.empty()) obj["detail"] = ci.detail;
    if (!ci.documentation.empty()) {
        obj["documentation"] = llvm::json::Object{
            {"kind", "markdown"},
            {"value", ci.documentation}
        };
    }
    if (!ci.sortText.empty()) obj["sortText"] = ci.sortText;
    if (!ci.filterText.empty()) obj["filterText"] = ci.filterText;
    if (!ci.insertText.empty()) obj["insertText"] = ci.insertText;
    if (ci.insertTextFormat != 1) obj["insertTextFormat"] = ci.insertTextFormat;
    if (ci.textEdit) obj["textEdit"] = toJson(*ci.textEdit);
    if (!ci.additionalTextEdits.empty()) {
        llvm::json::Array arr;
        for (const auto& e : ci.additionalTextEdits) {
            arr.push_back(toJson(e));
        }
        obj["additionalTextEdits"] = std::move(arr);
    }
    return llvm::json::Value(std::move(obj));
}

llvm::json::Value toJson(const CompletionList& cl) {
    llvm::json::Array items;
    for (const auto& item : cl.items) {
        items.push_back(toJson(item));
    }
    return llvm::json::Object{
        {"isIncomplete", cl.isIncomplete},
        {"items", std::move(items)}
    };
}

llvm::json::Value toJson(const Hover& h) {
    llvm::json::Object obj{
        {"contents", llvm::json::Object{
            {"kind", "markdown"},
            {"value", h.contents}
        }}
    };
    if (h.range) {
        obj["range"] = toJson(*h.range);
    }
    return llvm::json::Value(std::move(obj));
}

llvm::json::Value toJson(const SignatureHelp& sh) {
    llvm::json::Array sigs;
    for (const auto& s : sh.signatures) {
        llvm::json::Array params;
        for (const auto& p : s.parameters) {
            llvm::json::Object pobj{{"label", p.label}};
            if (!p.documentation.empty()) {
                pobj["documentation"] = p.documentation;
            }
            params.push_back(std::move(pobj));
        }
        llvm::json::Object sobj{
            {"label", s.label},
            {"parameters", std::move(params)}
        };
        if (!s.documentation.empty()) {
            sobj["documentation"] = s.documentation;
        }
        sigs.push_back(std::move(sobj));
    }
    return llvm::json::Object{
        {"signatures", std::move(sigs)},
        {"activeSignature", sh.activeSignature},
        {"activeParameter", sh.activeParameter}
    };
}

llvm::json::Value toJson(const DocumentSymbol& ds) {
    llvm::json::Object obj{
        {"name", ds.name},
        {"kind", ds.kind},
        {"range", toJson(ds.range)},
        {"selectionRange", toJson(ds.selectionRange)}
    };
    if (!ds.detail.empty()) obj["detail"] = ds.detail;
    if (!ds.children.empty()) {
        llvm::json::Array children;
        for (const auto& c : ds.children) {
            children.push_back(toJson(c));
        }
        obj["children"] = std::move(children);
    }
    return llvm::json::Value(std::move(obj));
}

llvm::json::Value toJson(const WorkspaceEdit& we) {
    llvm::json::Object changes;
    for (const auto& [uri, edits] : we.changes) {
        llvm::json::Array arr;
        for (const auto& e : edits) {
            arr.push_back(toJson(e));
        }
        changes[uri] = std::move(arr);
    }
    return llvm::json::Object{
        {"changes", std::move(changes)}
    };
}

llvm::json::Value toJson(const OverridableMethodItem& item) {
    return llvm::json::Object{
        {"required", item.required},
        {"unimplemented", item.unimplemented},
        {"name", item.name},
        {"signature", item.signature},
        {"returnType", item.returnType},
        {"parent", item.parent}
    };
}

bool parsePosition(const llvm::json::Value& v, Position& out) {
    auto obj = v.getAsObject();
    if (!obj) return false;
    auto l = obj->getInteger("line");
    auto c = obj->getInteger("character");
    if (!l || !c) return false;
    out.line = static_cast<int>(*l);
    out.character = static_cast<int>(*c);
    return true;
}

bool parseRange(const llvm::json::Value& v, Range& out) {
    auto obj = v.getAsObject();
    if (!obj) return false;
    auto s = obj->get("start");
    auto e = obj->get("end");
    if (!s || !e) return false;
    return parsePosition(*s, out.start) && parsePosition(*e, out.end);
}

bool parseLocation(const llvm::json::Value& v, Location& out) {
    auto obj = v.getAsObject();
    if (!obj) return false;
    auto u = obj->getString("uri");
    auto r = obj->get("range");
    if (!u || !r) return false;
    out.uri = u->str();
    return parseRange(*r, out.range);
}

} // namespace javalsp
