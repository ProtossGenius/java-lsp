#pragma once

#include "semantics/symbol_table.h"
#include <string>
#include <vector>
#include <unordered_set>

namespace javalsp {

class DependencyResolver {
public:
    static DependencyResolver& instance();

    // Resolves and indexes JDK and workspace project dependencies into the given SymbolTable
    void resolveAll(const std::string& workspaceRoot, SymbolTable& symTab);

    // Get list of discovered dependency jar paths
    const std::vector<std::string>& getDiscoveredJars() const { return jarPaths_; }

private:
    DependencyResolver() = default;

    void resolveJdk(SymbolTable& symTab);
    void resolveMaven(const std::string& rootPath, SymbolTable& symTab);
    void resolveGradle(const std::string& rootPath, SymbolTable& symTab);
    void resolveClasspathFiles(const std::string& rootPath, SymbolTable& symTab);
    void indexJar(const std::string& jarPath, SymbolTable& symTab);

    std::string findJavaHome();

    std::unordered_set<std::string> indexedJars_;
    std::vector<std::string> jarPaths_;
    bool jdkResolved_ = false;
};

} // namespace javalsp
