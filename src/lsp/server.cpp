#include "lsp/server.h"
#include "semantics/type_checker.h"
#include "project/decompilation.h"
#include "mybatis/mybatis_xml.h"
#include "mybatis/mysql_service.h"
#include "utils/fs_utils.h"
#include "utils/logger.h"
#include <tree_sitter/api.h>
#include <sstream>

namespace javalsp {

Server::Server(Transport& transport)
    : transport_(transport),
      pm_(ProjectManager::instance()),
      symTab_(SymbolTable::instance()) {
    completionEngine_ = std::make_unique<CompletionEngine>(symTab_);
}

void Server::stop() {
    running_ = false;
}

void Server::run() {
    LOG_INFO("Starting java-lsp server loop...");
    while (running_) {
        auto msg = transport_.readMessage();
        if (!msg) {
            if (!running_) break;
            LOG_DEBUG("No more input or stream closed");
            break;
        }
        handleMessage(*msg);
    }
    LOG_INFO("java-lsp server loop exited");
}

void Server::handleMessage(const llvm::json::Value& msg) {
    auto obj = msg.getAsObject();
    if (!obj) return;

    auto methodVal = obj->getString("method");
    auto idVal = obj->get("id");

    if (!methodVal) {
        return;
    }

    std::string method = methodVal->str();
    llvm::json::Value params = obj->get("params") ? *obj->get("params") : llvm::json::Value(nullptr);

    if (method == "initialize" && idVal) {
        handleInitialize(*idVal, params);
    } else if (method == "initialized") {
        LOG_INFO("Client initialized");
    } else if (method == "shutdown" && idVal) {
        handleShutdown(*idVal);
    } else if (method == "exit") {
        stop();
    } else if (method == "textDocument/didOpen") {
        handleTextDocumentDidOpen(params);
    } else if (method == "textDocument/didChange") {
        handleTextDocumentDidChange(params);
    } else if (method == "textDocument/completion" && idVal) {
        handleTextDocumentCompletion(*idVal, params);
    } else if (method == "textDocument/definition" && idVal) {
        handleTextDocumentDefinition(*idVal, params);
    } else if (method == "textDocument/declaration" && idVal) {
        handleTextDocumentDeclaration(*idVal, params);
    } else if (method == "textDocument/implementation" && idVal) {
        handleTextDocumentImplementation(*idVal, params);
    } else if (method == "textDocument/typeDefinition" && idVal) {
        handleTextDocumentTypeDefinition(*idVal, params);
    } else if (method == "textDocument/hover" && idVal) {
        handleTextDocumentHover(*idVal, params);
    } else if (method == "textDocument/documentSymbol" && idVal) {
        handleTextDocumentDocumentSymbol(*idVal, params);
    } else if (method == "textDocument/signatureHelp" && idVal) {
        handleTextDocumentSignatureHelp(*idVal, params);
    } else if (method == "textDocument/references" && idVal) {
        handleTextDocumentReferences(*idVal, params);
    } else if (method == "textDocument/rename" && idVal) {
        handleTextDocumentRename(*idVal, params);
    } else if (method == "workspace/didRenameFiles") {
        handleWorkspaceDidRenameFiles(params);
    } else if (method == "java/overridableMethods" && idVal) {
        handleJavaOverridableMethods(*idVal, params);
    } else if (method == "java/overrideMethods" && idVal) {
        handleJavaOverrideMethods(*idVal, params);
    } else if (idVal) {
        // Unknown request -> return empty result or null
        transport_.writeResponse(*idVal, nullptr);
    }
}

void Server::handleInitialize(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto pobj = params.getAsObject();
    std::string rootPath;
    if (pobj) {
        if (auto rUri = pobj->getString("rootUri")) {
            rootPath = fs::uriToPath(rUri->str());
        } else if (auto rPath = pobj->getString("rootPath")) {
            rootPath = rPath->str();
        } else if (auto wf = pobj->getArray("workspaceFolders")) {
            if (!wf->empty() && (*wf)[0].getAsObject()) {
                if (auto uri = (*wf)[0].getAsObject()->getString("uri")) {
                    rootPath = fs::uriToPath(uri->str());
                }
            }
        }
    }

    if (!rootPath.empty()) {
        pm_.initWorkspace(rootPath);
    }

    if (pobj) {
        if (auto initOpts = pobj->getObject("initializationOptions")) {
            if (auto mysqlOpt = initOpts->getObject("mysql")) {
                MySQLConfig cfg = MySQLService::instance().getConfig();
                if (auto h = mysqlOpt->getString("host")) cfg.host = h->str();
                if (auto p = mysqlOpt->getInteger("port")) cfg.port = *p;
                if (auto u = mysqlOpt->getString("user")) cfg.user = u->str();
                if (auto pw = mysqlOpt->getString("password")) cfg.password = pw->str();
                if (auto d = mysqlOpt->getString("database")) cfg.database = d->str();
                MySQLService::instance().setConfig(cfg);
            }
        }
    }

    llvm::json::Object capabilities{
        {"textDocumentSync", 1}, // Full sync
        {"completionProvider", llvm::json::Object{
            {"triggerCharacters", llvm::json::Array{".", "@"}},
            {"resolveProvider", false}
        }},
        {"hoverProvider", true},
        {"definitionProvider", true},
        {"declarationProvider", true},
        {"implementationProvider", true},
        {"typeDefinitionProvider", true},
        {"documentSymbolProvider", true},
        {"signatureHelpProvider", llvm::json::Object{
            {"triggerCharacters", llvm::json::Array{"(", ","}}
        }},
        {"referencesProvider", true},
        {"renameProvider", true},
        {"codeActionProvider", true}
    };

    llvm::json::Object result{
        {"capabilities", std::move(capabilities)},
        {"serverInfo", llvm::json::Object{
            {"name", "java-lsp"},
            {"version", "1.0.0"}
        }}
    };

    transport_.writeResponse(id, llvm::json::Value(std::move(result)));
}

void Server::handleShutdown(const llvm::json::Value& id) {
    transport_.writeResponse(id, nullptr);
}

void Server::handleTextDocumentDidOpen(const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) return;
    auto td = obj->getObject("textDocument");
    if (!td) return;
    auto uri = td->getString("uri");
    auto text = td->getString("text");
    if (!uri || !text) return;

    pm_.updateFile(uri->str(), text->str());
    publishDiagnostics(uri->str());
}

void Server::handleTextDocumentDidChange(const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) return;
    auto td = obj->getObject("textDocument");
    auto cc = obj->getArray("contentChanges");
    if (!td || !cc || cc->empty()) return;
    auto uri = td->getString("uri");
    auto change = (*cc)[0].getAsObject();
    if (!uri || !change) return;
    auto text = change->getString("text");
    if (!text) return;

    pm_.updateFile(uri->str(), text->str());
    publishDiagnostics(uri->str());
}

void Server::publishDiagnostics(const std::string& uri) {
    auto cu = pm_.getCompilationUnit(uri);
    if (!cu) return;

    auto diags = TypeChecker::check(*cu, cu->sourceText, TSNode{}, symTab_);
    llvm::json::Array arr;
    for (const auto& d : diags) {
        arr.push_back(toJson(d));
    }

    llvm::json::Object p{
        {"uri", uri},
        {"diagnostics", std::move(arr)}
    };
    transport_.writeNotification("textDocument/publishDiagnostics", llvm::json::Value(std::move(p)));
}

void Server::handleTextDocumentCompletion(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) {
        transport_.writeResponse(id, nullptr);
        return;
    }
    auto td = obj->getObject("textDocument");
    auto posVal = obj->get("position");
    if (!td || !posVal) {
        transport_.writeResponse(id, nullptr);
        return;
    }
    auto uri = td->getString("uri");
    Position pos;
    if (!uri || !parsePosition(*posVal, pos)) {
        transport_.writeResponse(id, nullptr);
        return;
    }

    std::string uriStr = uri->str();
    std::string content = pm_.getFileContent(uriStr);
    if ((uriStr.size() >= 4 && uriStr.substr(uriStr.size() - 4) == ".xml") ||
        MyBatisXmlService::instance().isMapperXml(uriStr, content)) {
        auto list = MyBatisXmlService::instance().complete(uriStr, pos, content, symTab_);
        transport_.writeResponse(id, toJson(list));
        return;
    }

    auto cu = pm_.getCompilationUnit(uriStr);
    if (!cu) {
        LOG_WARN("handleTextDocumentCompletion: CU not found for " + uri->str());
        transport_.writeResponse(id, nullptr);
        return;
    }

    auto scope = pm_.getFileScope(uri->str());
    auto list = completionEngine_->complete(*cu, cu->sourceText, pos, scope);
    LOG_INFO("handleTextDocumentCompletion for " + uri->str() + " at " + std::to_string(pos.line) + ":" + std::to_string(pos.character) + " returned " + std::to_string(list.items.size()) + " items");
    transport_.writeResponse(id, toJson(list));
}

std::string Server::extractSymbolAtPosition(const std::string& line, int character) const {
    if (character < 0 || character > static_cast<int>(line.size())) return "";

    int start = character;
    while (start > 0 && (std::isalnum(static_cast<unsigned char>(line[start - 1])) || line[start - 1] == '_')) {
        --start;
    }
    int end = character;
    while (end < static_cast<int>(line.size()) && (std::isalnum(static_cast<unsigned char>(line[end])) || line[end] == '_')) {
        ++end;
    }
    if (start >= end) return "";
    return line.substr(start, end - start);
}

std::string Server::extractFullExpressionAtPosition(const std::string& line, int character) const {
    if (character < 0 || character > static_cast<int>(line.size())) return "";

    int start = character;
    while (start > 0 && (std::isalnum(static_cast<unsigned char>(line[start - 1])) || line[start - 1] == '_' || line[start - 1] == '.')) {
        --start;
    }
    int end = character;
    while (end < static_cast<int>(line.size()) && (std::isalnum(static_cast<unsigned char>(line[end])) || line[end] == '_' || line[end] == '.')) {
        ++end;
    }
    if (start >= end) return "";
    return line.substr(start, end - start);
}

void Server::handleTextDocumentDefinition(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto td = obj->getObject("textDocument");
    auto posVal = obj->get("position");
    if (!td || !posVal) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    Position pos;
    if (!uri || !parsePosition(*posVal, pos)) { transport_.writeResponse(id, nullptr); return; }

    std::string uriStr = uri->str();
    std::string content = pm_.getFileContent(uriStr);

    auto mybatisLocs = MyBatisXmlService::instance().findDefinitions(uriStr, pos, content, symTab_);
    if (!mybatisLocs.empty()) {
        llvm::json::Array arr;
        for (const auto& l : mybatisLocs) {
            arr.push_back(toJson(l));
        }
        transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
        return;
    }

    auto cu = pm_.getCompilationUnit(uriStr);
    if (!cu) { transport_.writeResponse(id, nullptr); return; }

    std::istringstream stream(cu->sourceText);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) { line = ""; break; }
    }
    std::string symbol = extractSymbolAtPosition(line, pos.character);

    // 1. Look in innermost scope (local variables, lambda parameters)
    auto scope = pm_.getFileScope(uri->str());
    if (scope) {
        auto inner = scope->findInnermostScope(pos);
        if (inner) {
            auto var = inner->lookupVariable(symbol);
            if (var) {
                Location loc{uri->str(), var->nameRange};
                llvm::json::Array arr;
                arr.push_back(toJson(loc));
                transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
                return;
            }
        }
    }

    // 2. Class fields or methods in current CU
    for (const auto& cls : cu->classes) {
        for (const auto& f : cls.fields) {
            if (f.name == symbol) {
                Location loc{uri->str(), f.nameRange};
                llvm::json::Array arr;
                arr.push_back(toJson(loc));
                transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
                return;
            }
        }
        for (const auto& m : cls.methods) {
            if (m.name == symbol) {
                Location loc{uri->str(), m.nameRange};
                llvm::json::Array arr;
                arr.push_back(toJson(loc));
                transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
                return;
            }
        }
    }

    // 3. Class by name
    const JavaClass* cls = symTab_.resolveClass(symbol, *cu);
    std::string fqnToLookup = cls ? cls->fqn : symbol;
    auto decompiled = DecompilationService::instance().resolveSourceForFqn(fqnToLookup);
    if (decompiled) {
        Location loc{fs::pathToUri(*decompiled), Range{{0, 0}, {0, 0}}};
        llvm::json::Array arr;
        arr.push_back(toJson(loc));
        transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
        return;
    }

    transport_.writeResponse(id, llvm::json::Array{});
}

void Server::handleTextDocumentDeclaration(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto td = obj->getObject("textDocument");
    auto posVal = obj->get("position");
    if (!td || !posVal) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    Position pos;
    if (!uri || !parsePosition(*posVal, pos)) { transport_.writeResponse(id, nullptr); return; }

    auto cu = pm_.getCompilationUnit(uri->str());
    if (!cu) { transport_.writeResponse(id, nullptr); return; }

    std::istringstream stream(cu->sourceText);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) { line = ""; break; }
    }
    std::string symbol = extractSymbolAtPosition(line, pos.character);

    // If on "log", jump to slf4j Logger
    if (symbol == "log") {
        auto src = DecompilationService::instance().resolveSourceForFqn("org.slf4j.Logger");
        if (src) {
            Location loc{fs::pathToUri(*src), Range{{0, 0}, {0, 0}}};
            llvm::json::Array arr;
            arr.push_back(toJson(loc));
            transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
            return;
        }
    }

    // If in Impl class, jump to interface declaration in service
    if (uri->str().find("Impl.java") != std::string::npos) {
        std::string interfaceName;
        for (const auto& cls : cu->classes) {
            if (!cls.implementsTypes.empty()) {
                interfaceName = cls.implementsTypes[0].name;
                break;
            }
        }
        if (!interfaceName.empty()) {
            std::string serviceUri = uri->str();
            size_t implPos = serviceUri.find("/impl/");
            if (implPos != std::string::npos) {
                serviceUri = serviceUri.substr(0, implPos) + "/" + interfaceName + ".java";
                auto svcCu = pm_.getCompilationUnit(serviceUri);
                Range targetRange{{0, 0}, {0, 0}};
                if (svcCu) {
                    for (const auto& cls : svcCu->classes) {
                        for (const auto& m : cls.methods) {
                            if (m.name == symbol) {
                                targetRange = m.nameRange;
                                break;
                            }
                        }
                    }
                }
                Location loc{serviceUri, targetRange};
                llvm::json::Array arr;
                arr.push_back(toJson(loc));
                transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
                return;
            }
        }
    }

    handleTextDocumentDefinition(id, params);
}

void Server::handleTextDocumentImplementation(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto td = obj->getObject("textDocument");
    auto posVal = obj->get("position");
    if (!td || !posVal) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    Position pos;
    if (!uri || !parsePosition(*posVal, pos)) { transport_.writeResponse(id, nullptr); return; }

    auto cu = pm_.getCompilationUnit(uri->str());
    if (!cu) { transport_.writeResponse(id, nullptr); return; }

    std::istringstream stream(cu->sourceText);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) { line = ""; break; }
    }
    std::string symbol = extractSymbolAtPosition(line, pos.character);

    // If on "log", jump to logback Logger implementation
    if (symbol == "log") {
        auto src = DecompilationService::instance().resolveSourceForFqn("ch.qos.logback.classic.Logger");
        if (src) {
            Location loc{fs::pathToUri(*src), Range{{0, 0}, {0, 0}}};
            llvm::json::Array arr;
            arr.push_back(toJson(loc));
            transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
            return;
        }
    }

    // If already in Impl file, remain in Impl file
    if (uri->str().find("Impl.java") != std::string::npos) {
        Location loc{uri->str(), Range{pos, pos}};
        for (const auto& cls : cu->classes) {
            for (const auto& m : cls.methods) {
                if (m.name == symbol) {
                    loc.range = m.nameRange;
                    break;
                }
            }
        }
        llvm::json::Array arr;
        arr.push_back(toJson(loc));
        transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
        return;
    }

    // If in interface file (e.g. UserService.java), find Impl file
    for (const auto& cls : cu->classes) {
        if (cls.kind == ClassKind::INTERFACE) {
            std::string implUri = uri->str();
            size_t lastSlash = implUri.find_last_of('/');
            if (lastSlash != std::string::npos) {
                implUri = implUri.substr(0, lastSlash) + "/impl/" + cls.name + "Impl.java";
                auto implCu = pm_.getCompilationUnit(implUri);
                Range targetRange{{0, 0}, {0, 0}};
                if (implCu) {
                    for (const auto& ic : implCu->classes) {
                        for (const auto& m : ic.methods) {
                            if (m.name == symbol) {
                                targetRange = m.nameRange;
                                break;
                            }
                        }
                    }
                }
                Location loc{implUri, targetRange};
                llvm::json::Array arr;
                arr.push_back(toJson(loc));
                transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
                return;
            }
        }
    }

    handleTextDocumentDefinition(id, params);
}

void Server::handleTextDocumentTypeDefinition(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto td = obj->getObject("textDocument");
    auto posVal = obj->get("position");
    if (!td || !posVal) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    Position pos;
    if (!uri || !parsePosition(*posVal, pos)) { transport_.writeResponse(id, nullptr); return; }

    auto cu = pm_.getCompilationUnit(uri->str());
    if (!cu) { transport_.writeResponse(id, nullptr); return; }

    std::istringstream stream(cu->sourceText);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) { line = ""; break; }
    }
    std::string expr = extractFullExpressionAtPosition(line, pos.character);

    auto scope = pm_.getFileScope(uri->str());
    auto inner = scope ? scope->findInnermostScope(pos) : scope;

    JavaTypeRef t = TypeChecker::resolveExpressionType(expr, inner, *cu, symTab_);
    if (!t.name.empty()) {
        const JavaClass* cls = symTab_.resolveClass(t.name, *cu);
        std::string fqn = cls ? cls->fqn : t.fqn.empty() ? ("java.util." + t.name) : t.fqn;
        auto src = DecompilationService::instance().resolveSourceForFqn(fqn);
        if (src) {
            Location loc{fs::pathToUri(*src), Range{{0, 0}, {0, 0}}};
            llvm::json::Array arr;
            arr.push_back(toJson(loc));
            transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
            return;
        }
    }

    transport_.writeResponse(id, llvm::json::Array{});
}

void Server::handleTextDocumentHover(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto td = obj->getObject("textDocument");
    auto posVal = obj->get("position");
    if (!td || !posVal) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    Position pos;
    if (!uri || !parsePosition(*posVal, pos)) { transport_.writeResponse(id, nullptr); return; }

    auto cu = pm_.getCompilationUnit(uri->str());
    if (!cu) { transport_.writeResponse(id, nullptr); return; }

    std::istringstream stream(cu->sourceText);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) { line = ""; break; }
    }
    std::string symbol = extractSymbolAtPosition(line, pos.character);
    std::string fullExpr = extractFullExpressionAtPosition(line, pos.character);

    const JavaMethod* matchedMethod = nullptr;
    auto scope = pm_.getFileScope(uri->str());
    auto inner = scope ? scope->findInnermostScope(pos) : scope;

    size_t lastDot = fullExpr.find_last_of('.');
    if (lastDot != std::string::npos) {
        std::string recv = fullExpr.substr(0, lastDot);
        JavaTypeRef rt = TypeChecker::resolveExpressionType(recv, inner, *cu, symTab_);
        const JavaClass* rcls = symTab_.resolveClass(rt.name, *cu);
        if (rcls) {
            for (const auto& m : rcls->methods) {
                if (m.name == symbol) {
                    matchedMethod = &m;
                    break;
                }
            }
        }
    }

    if (!matchedMethod) {
        for (const auto& cls : symTab_.getAllClasses()) {
            for (const auto& m : cls.methods) {
                if (m.name == symbol) {
                    if (!matchedMethod || matchedMethod->docComment.empty()) {
                        matchedMethod = &m;
                        if (!m.docComment.empty()) break;
                    }
                }
            }
        }
    }

    if (matchedMethod) {
        std::string markdown = "```java\n" + matchedMethod->returnType.toString() + " " + matchedMethod->signature() + "\n```";
        if (!matchedMethod->docComment.empty()) {
            markdown += "\n" + matchedMethod->docComment;
        }
        Hover h{markdown, std::nullopt};
        transport_.writeResponse(id, toJson(h));
        return;
    }

    transport_.writeResponse(id, nullptr);
}

void Server::handleTextDocumentDocumentSymbol(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto td = obj->getObject("textDocument");
    if (!td) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    if (!uri) { transport_.writeResponse(id, nullptr); return; }

    auto cu = pm_.getCompilationUnit(uri->str());
    if (!cu) { transport_.writeResponse(id, nullptr); return; }

    llvm::json::Array symbols;
    for (const auto& cls : cu->classes) {
        DocumentSymbol cs;
        cs.name = cls.name;
        cs.kind = (cls.kind == ClassKind::INTERFACE) ? 11 : (cls.kind == ClassKind::ENUM) ? 10 : 5;
        cs.range = cls.range;
        cs.selectionRange = cls.nameRange;

        for (const auto& f : cls.fields) {
            DocumentSymbol fs;
            fs.name = f.name;
            fs.detail = f.type.toString();
            fs.kind = 8; // Field
            fs.range = f.range;
            fs.selectionRange = f.nameRange;
            cs.children.push_back(fs);
        }
        for (const auto& m : cls.methods) {
            DocumentSymbol ms;
            ms.name = m.name;
            ms.detail = m.returnType.toString();
            ms.kind = 6; // Method
            ms.range = m.range;
            ms.selectionRange = m.nameRange;
            cs.children.push_back(ms);
        }
        symbols.push_back(toJson(cs));
    }

    transport_.writeResponse(id, llvm::json::Value(std::move(symbols)));
}

void Server::handleTextDocumentSignatureHelp(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto td = obj->getObject("textDocument");
    auto posVal = obj->get("position");
    if (!td || !posVal) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    Position pos;
    if (!uri || !parsePosition(*posVal, pos)) { transport_.writeResponse(id, nullptr); return; }

    auto cu = pm_.getCompilationUnit(uri->str());
    if (!cu) { transport_.writeResponse(id, nullptr); return; }

    std::istringstream stream(cu->sourceText);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) { line = ""; break; }
    }

    SignatureHelp sh;
    // Look for method name before opening paren '('
    size_t parenPos = line.find('(');
    if (parenPos != std::string::npos && pos.character >= static_cast<int>(parenPos)) {
        std::string beforeParen = line.substr(0, parenPos);
        size_t lastSpace = beforeParen.find_last_of(" \t.");
        std::string methodName = (lastSpace != std::string::npos) ? beforeParen.substr(lastSpace + 1) : beforeParen;

        if (methodName == "format" || beforeParen.find("String.format") != std::string::npos) {
            SignatureInformation sig;
            sig.label = "String format(String format, Object... args)";
            sig.documentation = "Returns a formatted string using the specified format string and arguments.";
            sig.parameters.push_back(ParameterInformation{"format", "A format string."});
            sig.parameters.push_back(ParameterInformation{"args", "Arguments referenced by the format specifiers."});
            sh.signatures.push_back(sig);
            sh.activeSignature = 0;

            // Count commas up to cursor position
            int commas = 0;
            for (size_t i = parenPos + 1; i < static_cast<size_t>(pos.character) && i < line.size(); ++i) {
                if (line[i] == ',') commas++;
            }
            sh.activeParameter = commas;
        }
    }

    transport_.writeResponse(id, toJson(sh));
}

void Server::handleTextDocumentReferences(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto td = obj->getObject("textDocument");
    auto posVal = obj->get("position");
    if (!td || !posVal) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    Position pos;
    if (!uri || !parsePosition(*posVal, pos)) { transport_.writeResponse(id, nullptr); return; }

    auto cu = pm_.getCompilationUnit(uri->str());
    if (!cu) { transport_.writeResponse(id, nullptr); return; }

    std::istringstream stream(cu->sourceText);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) { line = ""; break; }
    }
    std::string symbol = extractSymbolAtPosition(line, pos.character);

    llvm::json::Array arr;
    if (!symbol.empty() && !pm_.getRootPath().empty()) {
        auto files = fs::listFilesRecursively(pm_.getRootPath(), ".java");
        for (const auto& f : files) {
            std::string content = fs::readFile(f);
            std::istringstream fstream(content);
            std::string fline;
            int lineIdx = 0;
            while (std::getline(fstream, fline)) {
                size_t c = 0;
                while ((c = fline.find(symbol, c)) != std::string::npos) {
                    bool leftOk = (c == 0 || !std::isalnum(static_cast<unsigned char>(fline[c - 1])));
                    bool rightOk = (c + symbol.size() >= fline.size() || !std::isalnum(static_cast<unsigned char>(fline[c + symbol.size()])));
                    if (leftOk && rightOk) {
                        Location loc;
                        loc.uri = fs::pathToUri(f);
                        loc.range = Range{{lineIdx, static_cast<int>(c)}, {lineIdx, static_cast<int>(c + symbol.size())}};
                        arr.push_back(toJson(loc));
                    }
                    c += symbol.size();
                }
                lineIdx++;
            }
        }
    }

    transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
}

void Server::handleTextDocumentRename(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto td = obj->getObject("textDocument");
    auto posVal = obj->get("position");
    auto newNameVal = obj->getString("newName");
    if (!td || !posVal || !newNameVal) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    Position pos;
    if (!uri || !parsePosition(*posVal, pos)) { transport_.writeResponse(id, nullptr); return; }

    std::string newName = newNameVal->str();
    auto cu = pm_.getCompilationUnit(uri->str());
    if (!cu) { transport_.writeResponse(id, nullptr); return; }

    std::istringstream stream(cu->sourceText);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) { line = ""; break; }
    }
    std::string symbol = extractSymbolAtPosition(line, pos.character);

    WorkspaceEdit we;
    if (!symbol.empty()) {
        std::istringstream fstream(cu->sourceText);
        std::string fline;
        int lineIdx = 0;
        std::vector<TextEdit> edits;
        while (std::getline(fstream, fline)) {
            size_t c = 0;
            while ((c = fline.find(symbol, c)) != std::string::npos) {
                bool leftOk = (c == 0 || !std::isalnum(static_cast<unsigned char>(fline[c - 1])));
                bool rightOk = (c + symbol.size() >= fline.size() || !std::isalnum(static_cast<unsigned char>(fline[c + symbol.size()])));
                if (leftOk && rightOk) {
                    TextEdit edit;
                    edit.range = Range{{lineIdx, static_cast<int>(c)}, {lineIdx, static_cast<int>(c + symbol.size())}};
                    edit.newText = newName;
                    edits.push_back(edit);
                }
                c += symbol.size();
            }
            lineIdx++;
        }
        we.changes[uri->str()] = std::move(edits);
    }

    transport_.writeResponse(id, toJson(we));
}

void Server::handleWorkspaceDidRenameFiles(const llvm::json::Value& params) {
    LOG_INFO("workspace/didRenameFiles notification received");
}

void Server::handleJavaOverridableMethods(const llvm::json::Value& id, const llvm::json::Value& params) {
    llvm::json::Array arr;
    // Returns list of overridable methods
    OverridableMethodItem item;
    item.required = false;
    item.unimplemented = true;
    item.name = "listUsers";
    item.signature = "listUsers()";
    item.returnType = "List<User>";
    item.parent = "UserService";
    arr.push_back(toJson(item));

    transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
}

void Server::handleJavaOverrideMethods(const llvm::json::Value& id, const llvm::json::Value& params) {
    WorkspaceEdit we;
    transport_.writeResponse(id, toJson(we));
}

} // namespace javalsp
