#include "project/project_manager.h"
#include "project/dependency_resolver.h"
#include "mybatis/mybatis_xml.h"
#include "mybatis/mysql_service.h"
#include "semantics/lombok.h"
#include "semantics/nested_lambda.h"
#include "utils/fs_utils.h"
#include "utils/logger.h"

namespace javalsp {

ProjectManager& ProjectManager::instance() {
    static ProjectManager inst;
    return inst;
}

ProjectManager::ProjectManager()
    : symTab_(SymbolTable::instance()) {
    parser_.init();
}

void ProjectManager::initWorkspace(const std::string& rootPath) {
    std::lock_guard<std::mutex> lock(mutex_);
    rootPath_ = fs::normalizePath(rootPath);
    LOG_INFO("Initializing workspace at " + rootPath_);
    indexWorkspace();
}

void ProjectManager::indexWorkspace() {
    if (rootPath_.empty()) return;

    auto javaFiles = fs::listFilesRecursively(rootPath_, ".java");
    LOG_INFO("Found " + std::to_string(javaFiles.size()) + " Java files in workspace");

    for (const auto& path : javaFiles) {
        indexJavaFile(path);
    }

    // Index MyBatis XML files
    auto xmlFiles = fs::listFilesRecursively(rootPath_, ".xml");
    for (const auto& xf : xmlFiles) {
        std::string content = fs::readFile(xf);
        std::string uri = fs::pathToUri(xf);
        if (MyBatisXmlService::instance().isMapperXml(uri, content)) {
            MyBatisXmlService::instance().indexMapperXml(uri, content);
            fileBuffers_[uri] = content;
        }
    }

    // Initialize MySQL configuration
    MySQLService::instance().detectConfig(rootPath_);

    // Resolve JDK and external dependencies
    DependencyResolver::instance().resolveAll(rootPath_, symTab_);
}

void ProjectManager::indexJavaFile(const std::string& path) {
    std::string content = fs::readFile(path);
    if (content.empty()) return;

    auto cu = parser_.parse(content, path);
    if (!cu) return;

    // Process Lombok for each class
    for (auto& cls : cu->classes) {
        cls.filePath = path;
        LombokHandler::processClass(cls);
        symTab_.addClass(cls);
    }

    std::string uri = fs::pathToUri(path);
    fileCUs_[uri] = cu;
    fileBuffers_[uri] = content;

    // Build scopes
    auto fileScope = Scope::create(ScopeKind::FILE);
    for (const auto& cls : cu->classes) {
        auto classScope = Scope::create(ScopeKind::CLASS, fileScope, cls.range);
        for (const auto& f : cls.fields) {
            JavaVariable var;
            var.name = f.name;
            var.type = f.type;
            var.range = f.range;
            var.nameRange = f.nameRange;
            classScope->addVariable(var);
        }

        for (const auto& m : cls.methods) {
            auto methodScope = Scope::create(ScopeKind::METHOD, classScope, m.range);
            for (const auto& p : m.parameters) {
                JavaVariable pvar;
                pvar.name = p.second;
                pvar.type = p.first;
                pvar.range = m.range;
                methodScope->addVariable(pvar);
            }
        }
    }
    fileScopes_[uri] = fileScope;
}

std::shared_ptr<JavaCompilationUnit> ProjectManager::getCompilationUnit(const std::string& uri) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = fileCUs_.find(uri);
    if (it != fileCUs_.end()) {
        return it->second;
    }
    std::string path = fs::uriToPath(uri);
    if (fs::fileExists(path)) {
        indexJavaFile(path);
        auto it2 = fileCUs_.find(uri);
        if (it2 != fileCUs_.end()) return it2->second;
    }
    return nullptr;
}

void ProjectManager::updateFile(const std::string& uri, const std::string& newContent) {
    std::lock_guard<std::mutex> lock(mutex_);
    fileBuffers_[uri] = newContent;

    // Handle MyBatis XML
    if ((uri.size() >= 4 && uri.substr(uri.size() - 4) == ".xml") ||
        MyBatisXmlService::instance().isMapperXml(uri, newContent)) {
        MyBatisXmlService::instance().indexMapperXml(uri, newContent);
        return;
    }

    std::string path = fs::uriToPath(uri);

    auto cu = parser_.parse(newContent, path);
    if (!cu) return;

    auto oldIt = fileCUs_.find(uri);
    if (cu->classes.empty() && oldIt != fileCUs_.end() && oldIt->second && !oldIt->second->classes.empty()) {
        cu->classes = oldIt->second->classes;
        if (cu->packageName.empty()) cu->packageName = oldIt->second->packageName;
        if (cu->imports.empty()) cu->imports = oldIt->second->imports;
    }

    for (auto& cls : cu->classes) {
        cls.filePath = path;
        LombokHandler::processClass(cls);
        symTab_.addClass(cls);
    }
    fileCUs_[uri] = cu;

    // Rebuild scope hierarchy
    auto fileScope = Scope::create(ScopeKind::FILE);
    for (const auto& cls : cu->classes) {
        auto classScope = Scope::create(ScopeKind::CLASS, fileScope, cls.range);
        for (const auto& f : cls.fields) {
            JavaVariable var;
            var.name = f.name;
            var.type = f.type;
            var.range = f.range;
            var.nameRange = f.nameRange;
            classScope->addVariable(var);
        }

        for (const auto& m : cls.methods) {
            auto methodScope = Scope::create(ScopeKind::METHOD, classScope, m.range);
            for (const auto& p : m.parameters) {
                JavaVariable pvar;
                pvar.name = p.second;
                pvar.type = p.first;
                pvar.range = m.range;
                methodScope->addVariable(pvar);
            }
        }
    }
    fileScopes_[uri] = fileScope;
}

void ProjectManager::closeFile(const std::string& uri) {
    // Keep cached CU for cross-file navigation
}

std::string ProjectManager::getFileContent(const std::string& uri) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = fileBuffers_.find(uri);
    if (it != fileBuffers_.end()) return it->second;
    std::string path = fs::uriToPath(uri);
    std::string content = fs::readFile(path);
    if (!content.empty()) {
        fileBuffers_[uri] = content;
    }
    return content;
}

std::shared_ptr<Scope> ProjectManager::getFileScope(const std::string& uri) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = fileScopes_.find(uri);
    if (it != fileScopes_.end()) return it->second;
    return nullptr;
}

} // namespace javalsp
