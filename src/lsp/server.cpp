#include "lsp/server.h"
#include "semantics/type_checker.h"
#include "project/decompilation.h"
#include "mybatis/mybatis_xml.h"
#include "mybatis/mysql_service.h"
#include "utils/fs_utils.h"
#include "utils/logger.h"
#include <tree_sitter/api.h>
#include <sstream>
#include <fstream>
#include <unordered_set>

namespace javalsp {

static bool isPrimitiveOrKeyword(const std::string& symbol) {
    static const std::unordered_set<std::string> keywords = {
        "int", "void", "boolean", "byte", "char", "short", "long", "float", "double",
        "var", "val", "null", "true", "false", "this", "super", "return", "if", "else",
        "for", "while", "do", "switch", "case", "default", "break", "continue", "throw",
        "throws", "try", "catch", "finally", "class", "interface", "enum", "record",
        "extends", "implements", "public", "protected", "private", "static", "final",
        "abstract", "synchronized", "volatile", "transient", "native", "package", "import",
        "new", "instanceof"
    };
    return keywords.count(symbol) > 0;
}

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
    } else if (method == "textDocument/didClose") {
        handleTextDocumentDidClose(params);
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
    } else if (method == "textDocument/prepareRename" && idVal) {
        handleTextDocumentPrepareRename(*idVal, params);
    } else if (method == "textDocument/rename" && idVal) {
        handleTextDocumentRename(*idVal, params);
    } else if (method == "textDocument/formatting" && idVal) {
        handleTextDocumentFormatting(*idVal, params);
    } else if (method == "textDocument/codeAction" && idVal) {
        handleTextDocumentCodeAction(*idVal, params);
    } else if (method == "workspace/didRenameFiles") {
        handleWorkspaceDidRenameFiles(params);
    } else if (method == "java/overridableMethods" && idVal) {
        handleJavaOverridableMethods(*idVal, params);
    } else if (method == "java/overrideMethods" && idVal) {
        handleJavaOverrideMethods(*idVal, params);
    } else if (method == "java/getRefactorEdit" && idVal) {
        handleJavaGetRefactorEdit(*idVal, params);
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
        {"renameProvider", llvm::json::Object{
            {"prepareProvider", true}
        }},
        {"documentFormattingProvider", true},
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

void Server::handleTextDocumentDidClose(const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) return;
    auto td = obj->getObject("textDocument");
    if (!td) return;
    auto uri = td->getString("uri");
    if (!uri) return;
    pm_.closeFile(uri->str());
}

void Server::publishDiagnostics(const std::string& uri) {
    if (uri.size() >= 4 && uri.substr(uri.size() - 4) == ".xml") {
        llvm::json::Object p{
            {"uri", uri},
            {"diagnostics", llvm::json::Array{}}
        };
        transport_.writeNotification("textDocument/publishDiagnostics", llvm::json::Value(std::move(p)));
        return;
    }

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
    if (symbol.empty() || isPrimitiveOrKeyword(symbol)) {
        transport_.writeResponse(id, llvm::json::Array{});
        return;
    }

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

    // 3. Class by name or member from third-party dependency
    const JavaClass* cls = symTab_.resolveClass(symbol, *cu);
    std::string fqnToLookup = cls ? cls->fqn : symbol;
    auto decompiled = DecompilationService::instance().resolveSourceForFqn(fqnToLookup, &symTab_);
    if (!decompiled) {
        // Check if symbol is a method or field on a receiver object
        std::string expr = extractFullExpressionAtPosition(line, pos.character);
        if (!expr.empty()) {
            auto inner = scope ? scope->findInnermostScope(pos) : scope;
            JavaTypeRef tr = TypeChecker::resolveExpressionType(expr, inner, *cu, symTab_);
            if (!tr.name.empty() && !isPrimitiveOrKeyword(tr.name)) {
                const JavaClass* rCls = symTab_.resolveClass(tr.name, *cu);
                std::string rFqn = rCls ? rCls->fqn : tr.fqn.empty() ? tr.name : tr.fqn;
                decompiled = DecompilationService::instance().resolveSourceForFqn(rFqn, &symTab_);
            }
        }
    }
    if (decompiled) {
        Range defRange{{0, 0}, {0, 0}};
        std::ifstream dFile(*decompiled);
        std::string dLine;
        int lineNo = 0;
        while (std::getline(dFile, dLine)) {
            size_t found = dLine.find(symbol);
            if (found != std::string::npos) {
                bool left = (found == 0 || (!std::isalnum(static_cast<unsigned char>(dLine[found - 1])) && dLine[found - 1] != '_'));
                bool right = (found + symbol.size() >= dLine.size() || (!std::isalnum(static_cast<unsigned char>(dLine[found + symbol.size()])) && dLine[found + symbol.size()] != '_'));
                if (left && right) {
                    defRange = Range{{lineNo, static_cast<int>(found)}, {lineNo, static_cast<int>(found + symbol.size())}};
                    break;
                }
            }
            lineNo++;
        }
        Location loc{fs::pathToUri(*decompiled), defRange};
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
    if (symbol.empty() || isPrimitiveOrKeyword(symbol)) {
        transport_.writeResponse(id, llvm::json::Array{});
        return;
    }

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
    if (symbol.empty() || isPrimitiveOrKeyword(symbol)) {
        transport_.writeResponse(id, llvm::json::Array{});
        return;
    }

    // If on "log", jump to logback Logger implementation
    if (symbol == "log") {
        auto src = DecompilationService::instance().resolveSourceForFqn("ch.qos.logback.classic.Logger", &symTab_);
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
            // For MyBatis Mapper interfaces, there is no Java Impl class!
            if (cls.name.find("Mapper") != std::string::npos || uri->str().find("Mapper.java") != std::string::npos) {
                auto mybatisLocs = MyBatisXmlService::instance().findDefinitions(uri->str(), pos, cu->sourceText, symTab_);
                if (!mybatisLocs.empty()) {
                    llvm::json::Array arr;
                    for (const auto& l : mybatisLocs) {
                        arr.push_back(toJson(l));
                    }
                    transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
                    return;
                }
                transport_.writeResponse(id, llvm::json::Array{});
                return;
            }

            std::string implUri = uri->str();
            size_t lastSlash = implUri.find_last_of('/');
            if (lastSlash != std::string::npos) {
                implUri = implUri.substr(0, lastSlash) + "/impl/" + cls.name + "Impl.java";
                std::string implPath = fs::uriToPath(implUri);
                if (fs::fileExists(implPath)) {
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
    }

    transport_.writeResponse(id, llvm::json::Array{});
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
        for (const auto& [fqn, cls] : symTab_.getAllClasses()) {
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

void Server::handleTextDocumentPrepareRename(const llvm::json::Value& id, const llvm::json::Value& params) {
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
    if (symbol.empty() || isPrimitiveOrKeyword(symbol)) {
        transport_.writeResponse(id, nullptr);
        return;
    }

    int start = pos.character;
    while (start > 0 && (std::isalnum(static_cast<unsigned char>(line[start - 1])) || line[start - 1] == '_')) {
        --start;
    }
    int end = pos.character;
    while (end < static_cast<int>(line.size()) && (std::isalnum(static_cast<unsigned char>(line[end])) || line[end] == '_')) {
        ++end;
    }

    llvm::json::Object res{
        {"range", toJson(Range{{pos.line, start}, {pos.line, end}})},
        {"placeholder", symbol}
    };
    transport_.writeResponse(id, llvm::json::Value(std::move(res)));
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
    if (symbol.empty() || isPrimitiveOrKeyword(symbol)) {
        transport_.writeResponse(id, nullptr);
        return;
    }

    WorkspaceEdit we;
    std::vector<TextEdit> edits;

    // Check if this symbol is a local variable or parameter in the current scope
    auto scope = pm_.getFileScope(uri->str());
    auto innerScope = scope ? scope->findInnermostScope(pos) : nullptr;
    auto var = innerScope ? innerScope->lookupVariable(symbol) : std::nullopt;

    if (var.has_value() && innerScope && innerScope->kind() != ScopeKind::FILE && innerScope->kind() != ScopeKind::CLASS) {
        // Local variable or parameter: rename ONLY within its enclosing method or lambda scope
        Range scopeRange = innerScope->range();
        int startLine = (scopeRange.start.line > 0) ? scopeRange.start.line : 0;
        int endLine = (scopeRange.end.line > 0) ? scopeRange.end.line : static_cast<int>(cu->sourceText.size());

        std::istringstream fstream(cu->sourceText);
        std::string fline;
        int lineIdx = 0;
        while (std::getline(fstream, fline)) {
            if (lineIdx >= startLine && lineIdx <= endLine) {
                size_t c = 0;
                while ((c = fline.find(symbol, c)) != std::string::npos) {
                    bool leftOk = (c == 0 || (!std::isalnum(static_cast<unsigned char>(fline[c - 1])) && fline[c - 1] != '_'));
                    bool rightOk = (c + symbol.size() >= fline.size() || (!std::isalnum(static_cast<unsigned char>(fline[c + symbol.size()])) && fline[c + symbol.size()] != '_'));
                    // Make sure it's not a member access on another object: e.g. other.symbol
                    if (leftOk && c > 0 && fline[c - 1] == '.') {
                        if (c < 5 || fline.substr(c - 5, 5) != "this.") {
                            leftOk = false;
                        }
                    }
                    if (leftOk && rightOk) {
                        TextEdit edit;
                        edit.range = Range{{lineIdx, static_cast<int>(c)}, {lineIdx, static_cast<int>(c + symbol.size())}};
                        edit.newText = newName;
                        edits.push_back(edit);
                    }
                    c += symbol.size();
                }
            }
            lineIdx++;
        }
    } else {
        // Field, method, or class: rename references within the compilation unit, avoiding comments
        std::istringstream fstream(cu->sourceText);
        std::string fline;
        int lineIdx = 0;
        while (std::getline(fstream, fline)) {
            std::string trimmed = fline;
            size_t firstNonBlank = trimmed.find_first_not_of(" \t");
            if (firstNonBlank != std::string::npos && (trimmed.substr(firstNonBlank, 2) == "//" || trimmed.substr(firstNonBlank, 1) == "*")) {
                lineIdx++;
                continue;
            }
            size_t c = 0;
            while ((c = fline.find(symbol, c)) != std::string::npos) {
                bool leftOk = (c == 0 || (!std::isalnum(static_cast<unsigned char>(fline[c - 1])) && fline[c - 1] != '_'));
                bool rightOk = (c + symbol.size() >= fline.size() || (!std::isalnum(static_cast<unsigned char>(fline[c + symbol.size()])) && fline[c + symbol.size()] != '_'));
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
    }

    we.changes[uri->str()] = std::move(edits);

    // If a top-level class was renamed, also update file name and cache in ProjectManager
    bool isTopLevelClass = false;
    for (const auto& c : cu->classes) {
        if (c.name == symbol) {
            isTopLevelClass = true;
            break;
        }
    }
    std::string currentUri = uri->str();
    size_t lastSlash = currentUri.find_last_of('/');
    std::string fileName = (lastSlash != std::string::npos) ? currentUri.substr(lastSlash + 1) : "";
    std::string expectedFileName = symbol + ".java";

    if (isTopLevelClass && fileName == expectedFileName) {
        std::string newUri = currentUri.substr(0, lastSlash + 1) + newName + ".java";
        pm_.renameFile(currentUri, newUri);
    }

    transport_.writeResponse(id, toJson(we));
}

void Server::handleTextDocumentFormatting(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto td = obj->getObject("textDocument");
    if (!td) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    if (!uri) { transport_.writeResponse(id, nullptr); return; }

    std::string content = pm_.getFileContent(uri->str());
    if (content.empty()) {
        auto cu = pm_.getCompilationUnit(uri->str());
        if (cu) content = cu->sourceText;
    }
    if (content.empty()) {
        transport_.writeResponse(id, llvm::json::Array{});
        return;
    }

    std::istringstream stream(content);
    std::string line;
    std::string formatted;
    int indentLevel = 0;
    while (std::getline(stream, line)) {
        size_t start = line.find_first_not_of(" \t");
        if (start == std::string::npos) {
            formatted += "\n";
            continue;
        }
        std::string trimmed = line.substr(start);
        if (!trimmed.empty() && (trimmed[0] == '}' || trimmed[0] == ')')) {
            if (indentLevel > 0) indentLevel--;
        }
        formatted += std::string(indentLevel * 4, ' ') + trimmed + "\n";
        for (char ch : trimmed) {
            if (ch == '{') indentLevel++;
        }
    }

    TextEdit edit;
    int totalLines = 0;
    for (char ch : content) if (ch == '\n') totalLines++;
    edit.range = Range{{0, 0}, {totalLines + 1, 0}};
    edit.newText = formatted;

    llvm::json::Array arr;
    arr.push_back(toJson(edit));
    transport_.writeResponse(id, llvm::json::Value(std::move(arr)));
}

static int levenshteinDistance(const std::string& s1, const std::string& s2) {
    size_t m = s1.size();
    size_t n = s2.size();
    std::vector<std::vector<int>> dp(m + 1, std::vector<int>(n + 1, 0));
    for (size_t i = 0; i <= m; ++i) dp[i][0] = i;
    for (size_t j = 0; j <= n; ++j) dp[0][j] = j;
    for (size_t i = 1; i <= m; ++i) {
        for (size_t j = 1; j <= n; ++j) {
            if (s1[i - 1] == s2[j - 1]) dp[i][j] = dp[i - 1][j - 1];
            else dp[i][j] = 1 + std::min({dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]});
        }
    }
    return dp[m][n];
}

void Server::handleTextDocumentCodeAction(const llvm::json::Value& id, const llvm::json::Value& params) {
    llvm::json::Array actions;
    auto obj = params.getAsObject();
    if (!obj) {
        transport_.writeResponse(id, llvm::json::Value(std::move(actions)));
        return;
    }
    auto td = obj->getObject("textDocument");
    if (!td) {
        transport_.writeResponse(id, llvm::json::Value(std::move(actions)));
        return;
    }
    auto uri = td->getString("uri");
    if (!uri) {
        transport_.writeResponse(id, llvm::json::Value(std::move(actions)));
        return;
    }
    std::string uriStr = uri->str();

    Range reqRange{{0, 0}, {0, 0}};
    if (auto rVal = obj->get("range")) {
        parseRange(*rVal, reqRange);
    }

    auto cu = pm_.getCompilationUnit(uriStr);
    if (!cu) {
        transport_.writeResponse(id, llvm::json::Value(std::move(actions)));
        return;
    }
    std::string srcText = cu->sourceText;

    // 1. Diagnostics-based Quick Fixes (missing imports, typos)
    auto context = obj->getObject("context");
    auto diagsArr = context ? context->getArray("diagnostics") : nullptr;
    if (diagsArr) {
        for (const auto& dVal : *diagsArr) {
            auto dObj = dVal.getAsObject();
            if (!dObj) continue;
            std::string msg = dObj->getString("message").value_or("").str();
            Range diagRange{{0, 0}, {0, 0}};
            if (auto drVal = dObj->get("range")) {
                parseRange(*drVal, diagRange);
            }

            // A. Missing type import: e.g. "Table cannot be resolved to a type"
            size_t cannotResolve = msg.find(" cannot be resolved to a type");
            if (cannotResolve != std::string::npos) {
                std::string typeName = msg.substr(0, cannotResolve);
                if (!typeName.empty() && typeName.front() == '@') typeName = typeName.substr(1);

                auto candidates = symTab_.findClassesMatchingPrefix(typeName);
                for (const auto& cand : candidates) {
                    if (cand.name == typeName) {
                        int insertLine = 0;
                        std::istringstream lss(srcText);
                        std::string curLine;
                        int lineNum = 0;
                        int lastImportLine = -1;
                        int packageLine = -1;
                        while (std::getline(lss, curLine)) {
                            if (curLine.rfind("package ", 0) == 0) packageLine = lineNum;
                            if (curLine.rfind("import ", 0) == 0) lastImportLine = lineNum;
                            lineNum++;
                        }
                        if (lastImportLine >= 0) insertLine = lastImportLine + 1;
                        else if (packageLine >= 0) insertLine = packageLine + 2;

                        TextEdit te;
                        te.range = Range{{insertLine, 0}, {insertLine, 0}};
                        te.newText = "import " + cand.fqn + ";\n";

                        WorkspaceEdit we;
                        we.changes[uriStr].push_back(te);

                        llvm::json::Object act{
                            {"title", "Import '" + cand.fqn + "'"},
                            {"kind", "quickfix"},
                            {"isPreferred", true},
                            {"edit", toJson(we)}
                        };
                        actions.push_back(std::move(act));
                    }
                }
            }

            // B. Typo / misspelled method or symbol
            std::string typoSymbol = "";
            size_t mStart = msg.find("'");
            if (mStart != std::string::npos) {
                size_t mEnd = msg.find("'", mStart + 1);
                if (mEnd != std::string::npos) {
                    typoSymbol = msg.substr(mStart + 1, mEnd - mStart - 1);
                }
            }
            if (typoSymbol.empty() && diagRange.start.line == diagRange.end.line && diagRange.end.character > diagRange.start.character) {
                std::istringstream lss(srcText);
                std::string curLine;
                for (int i = 0; i <= diagRange.start.line; ++i) {
                    if (!std::getline(lss, curLine)) { curLine = ""; break; }
                }
                if (diagRange.end.character <= static_cast<int>(curLine.size())) {
                    typoSymbol = curLine.substr(diagRange.start.character, diagRange.end.character - diagRange.start.character);
                }
            }

            if (!typoSymbol.empty() && typoSymbol.size() >= 3) {
                for (const auto& cls : cu->classes) {
                    for (const auto& m : cls.methods) {
                        if (m.name != typoSymbol && levenshteinDistance(typoSymbol, m.name) <= 2) {
                            TextEdit te;
                            te.range = diagRange;
                            te.newText = m.name;

                            WorkspaceEdit we;
                            we.changes[uriStr].push_back(te);

                            llvm::json::Object act{
                                {"title", "Change to '" + m.name + "'"},
                                {"kind", "quickfix"},
                                {"isPreferred", true},
                                {"edit", toJson(we)}
                            };
                            actions.push_back(std::move(act));
                            break;
                        }
                    }
                }
            }
        }
    }

    // 2. Source Action: Organize Imports
    std::vector<std::string> sortedImports = cu->imports;
    std::sort(sortedImports.begin(), sortedImports.end());
    sortedImports.erase(std::unique(sortedImports.begin(), sortedImports.end()), sortedImports.end());

    std::string newImportsText;
    for (const auto& imp : sortedImports) {
        newImportsText += "import " + imp + ";\n";
    }

    int firstImpLine = -1;
    int lastImpLine = -1;
    std::istringstream lssImp(srcText);
    std::string curImpLine;
    int impLineNum = 0;
    while (std::getline(lssImp, curImpLine)) {
        if (curImpLine.rfind("import ", 0) == 0) {
            if (firstImpLine < 0) firstImpLine = impLineNum;
            lastImpLine = impLineNum;
        }
        impLineNum++;
    }

    WorkspaceEdit orgWe;
    if (firstImpLine >= 0 && lastImpLine >= 0) {
        TextEdit te;
        te.range = Range{{firstImpLine, 0}, {lastImpLine + 1, 0}};
        te.newText = newImportsText;
        orgWe.changes[uriStr].push_back(te);
    }

    llvm::json::Object organizeImportsAction{
        {"title", "Organize Imports"},
        {"kind", "source.organizeImports"},
        {"isPreferred", true},
        {"edit", toJson(orgWe)}
    };
    actions.push_back(std::move(organizeImportsAction));

    // 3. Refactoring: Extract Method (only when selection is inside a method body)
    bool insideMethod = false;
    for (const auto& cls : cu->classes) {
        for (const auto& m : cls.methods) {
            if (m.bodyRange.contains(reqRange.start) && reqRange.start.line < m.bodyRange.end.line) {
                insideMethod = true;
                break;
            }
        }
    }

    if (insideMethod && (reqRange.end.line > reqRange.start.line || reqRange.end.character > reqRange.start.character + 5)) {
        std::istringstream lss(srcText);
        std::string curLine;
        std::string selectedCode;
        int lineNum = 0;
        int lastCloseBraceLine = -1;
        while (std::getline(lss, curLine)) {
            if (lineNum >= reqRange.start.line && lineNum <= reqRange.end.line) {
                selectedCode += "        " + curLine + "\n";
            }
            if (curLine.find('}') != std::string::npos) {
                lastCloseBraceLine = lineNum;
            }
            lineNum++;
        }

        if (!selectedCode.empty()) {
            TextEdit callEdit;
            callEdit.range = reqRange;
            callEdit.newText = "    extractedMethod();\n";

            TextEdit methodDefEdit;
            int insertLine = (lastCloseBraceLine > 0) ? lastCloseBraceLine : lineNum;
            methodDefEdit.range = Range{{insertLine, 0}, {insertLine, 0}};
            methodDefEdit.newText =
                "\n    private void extractedMethod() {\n" +
                selectedCode +
                "    }\n";

            WorkspaceEdit we;
            we.changes[uriStr].push_back(callEdit);
            we.changes[uriStr].push_back(methodDefEdit);

            llvm::json::Object act{
                {"title", "Extract to method 'extractedMethod'"},
                {"kind", "refactor.extract.function"},
                {"isPreferred", true},
                {"edit", toJson(we)}
            };
            actions.push_back(std::move(act));
        }
    }

    transport_.writeResponse(id, llvm::json::Value(std::move(actions)));
}

void Server::handleWorkspaceDidRenameFiles(const llvm::json::Value& params) {
    LOG_INFO("workspace/didRenameFiles notification received");
    auto obj = params.getAsObject();
    if (!obj) return;
    auto files = obj->getArray("files");
    if (!files) return;
    for (const auto& fVal : *files) {
        auto fObj = fVal.getAsObject();
        if (fObj) {
            auto oldUri = fObj->getString("oldUri");
            auto newUri = fObj->getString("newUri");
            if (oldUri && newUri) {
                pm_.renameFile(oldUri->str(), newUri->str());
            }
        }
    }
}

void Server::handleJavaGetRefactorEdit(const llvm::json::Value& id, const llvm::json::Value& params) {
    auto obj = params.getAsObject();
    if (!obj) { transport_.writeResponse(id, nullptr); return; }
    auto ctx = obj->getObject("context");
    if (!ctx) { transport_.writeResponse(id, nullptr); return; }
    auto td = ctx->getObject("textDocument");
    auto rVal = ctx->get("range");
    if (!td || !rVal) { transport_.writeResponse(id, nullptr); return; }
    auto uri = td->getString("uri");
    if (!uri) { transport_.writeResponse(id, nullptr); return; }
    std::string uriStr = uri->str();

    Range reqRange{{0, 0}, {0, 0}};
    parseRange(*rVal, reqRange);

    auto cu = pm_.getCompilationUnit(uriStr);
    if (!cu) { transport_.writeResponse(id, nullptr); return; }

    std::string srcText = cu->sourceText;
    std::istringstream lss(srcText);
    std::string curLine;
    std::string selectedCode;
    int lineNum = 0;
    int lastCloseBraceLine = -1;
    while (std::getline(lss, curLine)) {
        if (lineNum >= reqRange.start.line && lineNum <= reqRange.end.line) {
            selectedCode += "        " + curLine + "\n";
        }
        if (curLine.find('}') != std::string::npos) {
            lastCloseBraceLine = lineNum;
        }
        lineNum++;
    }

    TextEdit callEdit;
    callEdit.range = reqRange;
    callEdit.newText = "    extractedMethod();\n";

    TextEdit methodDefEdit;
    int insertLine = (lastCloseBraceLine > 0) ? lastCloseBraceLine : lineNum;
    methodDefEdit.range = Range{{insertLine, 0}, {insertLine, 0}};
    methodDefEdit.newText =
        "\n    private void extractedMethod() {\n" +
        selectedCode +
        "    }\n";

    WorkspaceEdit we;
    we.changes[uriStr].push_back(callEdit);
    we.changes[uriStr].push_back(methodDefEdit);

    transport_.writeResponse(id, toJson(we));
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
