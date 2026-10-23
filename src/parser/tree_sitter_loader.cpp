#include "parser/tree_sitter_loader.h"
#include "utils/logger.h"
#include "utils/fs_utils.h"
#include <dlfcn.h>
#include <vector>
#include <cstdlib>

namespace javalsp {

typedef const TSLanguage* (*TreeSitterJavaFn)();

TreeSitterLoader& TreeSitterLoader::instance() {
    static TreeSitterLoader inst;
    return inst;
}

TreeSitterLoader::~TreeSitterLoader() {
    if (libHandle_) {
        dlclose(libHandle_);
        libHandle_ = nullptr;
    }
}

bool TreeSitterLoader::initialize(const std::string& customLibPath) {
    if (language_) return true;

    std::vector<std::string> searchPaths;
    if (!customLibPath.empty()) {
        searchPaths.push_back(customLibPath);
    }

    const char* home = std::getenv("HOME");
    std::string homeStr = home ? home : "";

    searchPaths.push_back("/Users/suremoon/workspace/java-lsp/lib/tree-sitter-java.so");
    searchPaths.push_back("lib/tree-sitter-java.so");
    searchPaths.push_back("../lib/tree-sitter-java.so");
    if (!homeStr.empty()) {
        searchPaths.push_back(homeStr + "/.local/share/nvim/lazy/nvim-treesitter/parser/java.so");
    }

    for (const auto& path : searchPaths) {
        if (!fs::fileExists(path)) continue;

        void* handle = dlopen(path.c_str(), RTLD_NOW | RTLD_GLOBAL);
        if (!handle) {
            LOG_DEBUG("Failed to dlopen " + path + ": " + dlerror());
            continue;
        }

        TreeSitterJavaFn fn = (TreeSitterJavaFn)dlsym(handle, "tree_sitter_java");
        if (!fn) {
            LOG_DEBUG("Symbol tree_sitter_java not found in " + path);
            dlclose(handle);
            continue;
        }

        libHandle_ = handle;
        language_ = fn();
        LOG_INFO("Loaded tree-sitter Java parser from " + path);
        return true;
    }

    LOG_ERROR("Could not locate or load tree-sitter-java library");
    return false;
}

TSParser* TreeSitterLoader::newParser() const {
    if (!language_) return nullptr;
    TSParser* parser = ts_parser_new();
    ts_parser_set_language(parser, language_);
    return parser;
}

} // namespace javalsp
