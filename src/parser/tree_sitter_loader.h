#pragma once

#include <tree_sitter/api.h>
#include <string>

namespace javalsp {

class TreeSitterLoader {
public:
    static TreeSitterLoader& instance();

    bool initialize(const std::string& customLibPath = "");
    const TSLanguage* getLanguage() const { return language_; }
    TSParser* newParser() const;

private:
    TreeSitterLoader() = default;
    ~TreeSitterLoader();

    void* libHandle_{nullptr};
    const TSLanguage* language_{nullptr};
};

} // namespace javalsp
