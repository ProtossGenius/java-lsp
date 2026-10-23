#pragma once

#include "parser/java_ast.h"
#include "parser/java_parser.h"
#include "semantics/symbol_table.h"
#include "semantics/scope.h"
#include <string>
#include <vector>
#include <unordered_map>
#include <memory>
#include <mutex>

namespace javalsp {

class ProjectManager {
public:
    static ProjectManager& instance();

    void initWorkspace(const std::string& rootPath);
    void indexWorkspace();

    std::shared_ptr<JavaCompilationUnit> getCompilationUnit(const std::string& uri);
    void updateFile(const std::string& uri, const std::string& newContent);
    void closeFile(const std::string& uri);
    void removeFile(const std::string& uri);
    void renameFile(const std::string& oldUri, const std::string& newUri);

    std::string getFileContent(const std::string& uri);
    std::shared_ptr<Scope> getFileScope(const std::string& uri);

    std::string getRootPath() const { return rootPath_; }
    SymbolTable& getSymbolTable() { return symTab_; }

private:
    ProjectManager();
    std::string rootPath_;
    SymbolTable& symTab_;
    JavaParser parser_;

    std::unordered_map<std::string, std::shared_ptr<JavaCompilationUnit>> fileCUs_;
    std::unordered_map<std::string, std::shared_ptr<Scope>> fileScopes_;
    std::unordered_map<std::string, std::string> fileBuffers_;
    std::mutex mutex_;

    void indexJavaFile(const std::string& path);
};

} // namespace javalsp
