#pragma once

#include "parser/java_ast.h"
#include <tree_sitter/api.h>
#include <string>
#include <memory>

namespace javalsp {

class JavaParser {
public:
    JavaParser();
    ~JavaParser();

    bool init();
    std::shared_ptr<JavaCompilationUnit> parse(const std::string& sourceText, const std::string& filePath = "");

private:
    void parseNode(TSNode node, const std::string& src, JavaCompilationUnit& cu);
    void parsePackage(TSNode node, const std::string& src, JavaCompilationUnit& cu);
    void parseImport(TSNode node, const std::string& src, JavaCompilationUnit& cu);
    void parseClass(TSNode node, const std::string& src, JavaCompilationUnit& cu, JavaClass* parentClass = nullptr);
    void parseField(TSNode node, const std::string& src, JavaClass& cls);
    void parseMethod(TSNode node, const std::string& src, JavaClass& cls);
    std::shared_ptr<JavaLambda> parseLambda(TSNode node, const std::string& src, std::shared_ptr<JavaLambda> parentLambda);

    // Helpers
    std::string getNodeText(TSNode node, const std::string& src) const;
    Range getNodeRange(TSNode node) const;
    JavaTypeRef parseType(TSNode node, const std::string& src) const;
    std::vector<std::string> parseModifiersAndAnnotations(TSNode node, const std::string& src, std::vector<std::string>& annotations) const;

    TSParser* parser_{nullptr};
};

} // namespace javalsp
