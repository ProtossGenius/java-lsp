#pragma once

#include "parser/java_ast.h"
#include <string>
#include <vector>

namespace javalsp {

class JarIndexer {
public:
    // Extract class stubs (name, packageName, fqn) from a .jar, .zip, or .jmod file
    static std::vector<JavaClass> indexArchive(const std::string& archivePath);

    // Scan an uncompressed directory containing .class files
    static std::vector<JavaClass> indexDirectory(const std::string& dirPath);

private:
    static std::vector<std::string> readZipEntries(const std::string& zipPath);
};

} // namespace javalsp
