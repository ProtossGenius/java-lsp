#pragma once

#include "parser/java_ast.h"
#include <string>
#include <vector>
#include <unordered_map>
#include <memory>
#include <optional>

namespace javalsp {

class SymbolTable {
public:
    static SymbolTable& instance();

    void clear();
    void addClass(const JavaClass& cls);
    const JavaClass* findClassByFqn(const std::string& fqn) const;
    const JavaClass* findClassByName(const std::string& simpleName) const;
    const JavaClass* resolveClass(const std::string& simpleName, const JavaCompilationUnit& cu) const;

    const std::unordered_map<std::string, JavaClass>& getAllClasses() const;
    std::vector<const JavaClass*> getClassesInPackage(const std::string& packageName) const;
    std::vector<JavaClass> findClassesMatchingPrefix(const std::string& prefix) const;
    void registerJdkTypes();

private:
    SymbolTable();
    std::unordered_map<std::string, JavaClass> classesByFqn_;
    std::unordered_map<std::string, std::vector<std::string>> fqnsByName_;
    std::unordered_map<std::string, std::vector<const JavaClass*>> classesByPackage_;
    std::unordered_map<std::string, std::string> normalizedToActualFqn_;
};

} // namespace javalsp
