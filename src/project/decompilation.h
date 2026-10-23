#pragma once

#include <string>
#include <optional>

namespace javalsp {

class DecompilationService {
public:
    static DecompilationService& instance();

    void setCacheDir(const std::string& dir);
    std::string getCacheDir() const { return cacheDir_; }

    std::optional<std::string> getJdkSource(const std::string& className);
    std::optional<std::string> getJarSource(const std::string& jarPath, const std::string& classFqn);
    std::optional<std::string> decompileClass(const std::string& classFilePath);

    std::optional<std::string> resolveSourceForFqn(const std::string& fqn);

private:
    DecompilationService();
    std::string cacheDir_;
    std::string jdkSrcZip_;
    std::string cfrJarPath_;

    void locateJdkSrcZip();
    void locateCfrJar();
};

} // namespace javalsp
