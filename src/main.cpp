#include "lsp/transport.h"
#include "lsp/server.h"
#include "parser/tree_sitter_loader.h"
#include "project/decompilation.h"
#include "utils/logger.h"
#include "utils/fs_utils.h"
#include <iostream>
#include <string>

int main(int argc, char* argv[]) {
    std::string storagePath = "/tmp/java-lsp";

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if ((arg == "-storage" || arg == "--storage") && i + 1 < argc) {
            storagePath = argv[++i];
        } else if (arg == "--version" || arg == "-v") {
            std::cout << "java-lsp version 1.0.0 (C++ / LLVM)" << std::endl;
            return 0;
        } else if (arg == "--help" || arg == "-h") {
            std::cout << "Usage: java-lsp [-storage <path>]" << std::endl;
            return 0;
        }
    }

    javalsp::fs::ensureDir(storagePath);
    javalsp::Logger::instance().init(storagePath + "/java-lsp.log", javalsp::LogLevel::INFO_LEVEL);
    LOG_INFO("java-lsp starting up with storage: " + storagePath);

    javalsp::DecompilationService::instance().setCacheDir(storagePath + "/sources");

    if (!javalsp::TreeSitterLoader::instance().initialize()) {
        LOG_WARN("Tree-sitter parser initialization fallback or pending");
    }

    javalsp::Transport transport;
    javalsp::Server server(transport);
    server.run();

    LOG_INFO("java-lsp shutting down.");
    return 0;
}
