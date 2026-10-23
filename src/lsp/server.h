#pragma once

#include "lsp/protocol.h"
#include "lsp/transport.h"
#include "project/project_manager.h"
#include "completion/completion_engine.h"
#include "semantics/symbol_table.h"
#include <memory>
#include <atomic>

namespace javalsp {

class Server {
public:
    Server(Transport& transport);

    void run();
    void stop();

    // LSP Handlers
    void handleMessage(const llvm::json::Value& msg);
    void handleInitialize(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleShutdown(const llvm::json::Value& id);
    void handleTextDocumentDidOpen(const llvm::json::Value& params);
    void handleTextDocumentDidChange(const llvm::json::Value& params);
    void handleTextDocumentCompletion(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleTextDocumentDefinition(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleTextDocumentDeclaration(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleTextDocumentImplementation(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleTextDocumentTypeDefinition(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleTextDocumentHover(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleTextDocumentDocumentSymbol(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleTextDocumentSignatureHelp(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleTextDocumentReferences(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleTextDocumentRename(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleWorkspaceDidRenameFiles(const llvm::json::Value& params);
    void handleJavaOverridableMethods(const llvm::json::Value& id, const llvm::json::Value& params);
    void handleJavaOverrideMethods(const llvm::json::Value& id, const llvm::json::Value& params);

    void publishDiagnostics(const std::string& uri);

private:
    Transport& transport_;
    ProjectManager& pm_;
    SymbolTable& symTab_;
    std::unique_ptr<CompletionEngine> completionEngine_;
    std::atomic<bool> running_{true};

    std::string extractSymbolAtPosition(const std::string& line, int character) const;
    std::string extractFullExpressionAtPosition(const std::string& line, int character) const;
};

} // namespace javalsp
