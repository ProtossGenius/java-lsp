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

    std::vector<JavaClass> getAllClasses() const;
    void registerJdkTypes();

private:
    SymbolTable();
    std::unordered_map<std::string, JavaClass> classesByFqn_;
    std::unordered_map<std::string, std::vector<std::string>> fqnsByName_;
};

} // namespace javalsp
