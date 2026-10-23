#include "project/decompilation.h"
#include "utils/fs_utils.h"
#include "utils/logger.h"
#include <cstdlib>

namespace javalsp {

DecompilationService& DecompilationService::instance() {
    static DecompilationService inst;
    return inst;
}

DecompilationService::DecompilationService() {
    const char* home = std::getenv("HOME");
    std::string homeStr = home ? home : "";
    cacheDir_ = homeStr + "/.cache/java-lsp/sources";
    fs::ensureDir(cacheDir_);

    locateJdkSrcZip();
    locateCfrJar();
}

void DecompilationService::setCacheDir(const std::string& dir) {
    cacheDir_ = dir;
    fs::ensureDir(cacheDir_);
}

void DecompilationService::locateJdkSrcZip() {
    std::vector<std::string> candidates = {
        "/opt/homebrew/Cellar/openjdk/25.0.2/libexec/openjdk.jdk/Contents/Home/lib/src.zip",
        "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home/lib/src.zip",
    };
    const char* javaHome = std::getenv("JAVA_HOME");
    if (javaHome) {
        candidates.insert(candidates.begin(), std::string(javaHome) + "/lib/src.zip");
    }

    for (const auto& c : candidates) {
        if (fs::fileExists(c)) {
            jdkSrcZip_ = c;
            LOG_INFO("Found JDK src.zip: " + c);
            return;
        }
    }
}

void DecompilationService::locateCfrJar() {
    std::vector<std::string> candidates = {
        "/Users/suremoon/workspace/java-lsp/lib/cfr.jar",
        "lib/cfr.jar",
        "../lib/cfr.jar"
    };
    const char* home = std::getenv("HOME");
    if (home) {
        candidates.push_back(std::string(home) + "/.m2/repository/org/benf/cfr/0.152/cfr-0.152.jar");
    }

    for (const auto& c : candidates) {
        if (fs::fileExists(c)) {
            cfrJarPath_ = c;
            LOG_INFO("Found CFR jar: " + c);
            return;
        }
    }
}

std::optional<std::string> DecompilationService::getJdkSource(const std::string& className) {
    if (jdkSrcZip_.empty() && !fs::fileExists(jdkSrcZip_)) {
        locateJdkSrcZip();
    }
    if (jdkSrcZip_.empty()) return std::nullopt;

    // className: e.g. "java.util.List" -> relative path "java/util/List.java"
    std::string relPath = className;
    for (char& c : relPath) if (c == '.') c = '/';
    relPath += ".java";

    std::string outPath = cacheDir_ + "/jdk/" + relPath;
    if (fs::fileExists(outPath)) {
        return outPath;
    }

    // Try in module java.base/
    std::string moduleEntry = "java.base/" + relPath;
    if (fs::extractZipEntry(jdkSrcZip_, moduleEntry, outPath)) {
        return outPath;
    }

    if (fs::extractZipEntry(jdkSrcZip_, relPath, outPath)) {
        return outPath;
    }

    return std::nullopt;
}

std::optional<std::string> DecompilationService::getJarSource(const std::string& jarPath, const std::string& classFqn) {
    std::string relPath = classFqn;
    for (char& c : relPath) if (c == '.') c = '/';
    relPath += ".java";

    std::string outPath = cacheDir_ + "/" + relPath;
    if (fs::fileExists(outPath)) {
        return outPath;
    }

    if (fs::extractZipEntry(jarPath, relPath, outPath)) {
        return outPath;
    }

    return std::nullopt;
}

std::optional<std::string> DecompilationService::decompileClass(const std::string& classFilePath) {
    if (cfrJarPath_.empty()) {
        locateCfrJar();
    }
    if (cfrJarPath_.empty()) return std::nullopt;

    std::string cmd = "java -jar \"" + cfrJarPath_ + "\" \"" + classFilePath + "\" --outputdir \"" + cacheDir_ + "\"";
    fs::runCommand(cmd);

    // Look for output file in cacheDir
    return cacheDir_;
}

std::optional<std::string> DecompilationService::resolveSourceForFqn(const std::string& fqn) {
    // 1. JDK standard library
    if (fqn.rfind("java.", 0) == 0 || fqn.rfind("javax.", 0) == 0) {
        auto jdk = getJdkSource(fqn);
        if (jdk) return jdk;
    }

    if (fqn.find('.') == std::string::npos) {
        auto langJdk = getJdkSource("java.lang." + fqn);
        if (langJdk) return langJdk;
        auto utilJdk = getJdkSource("java.util." + fqn);
        if (utilJdk) return utilJdk;
    }

    const char* home = std::getenv("HOME");
    std::string homeStr = home ? home : "";

    // 2. slf4j Logger
    if (fqn == "org.slf4j.Logger") {
        std::string slf4jSourcesJar = homeStr + "/.m2/repository/org/slf4j/slf4j-api/2.0.13/slf4j-api-2.0.13-sources.jar";
        if (fs::fileExists(slf4jSourcesJar)) {
            std::string outPath = cacheDir_ + "/org/slf4j/Logger.java";
            if (fs::fileExists(outPath)) return outPath;
            if (fs::extractZipEntry(slf4jSourcesJar, "org/slf4j/Logger.java", outPath)) {
                return outPath;
            }
        }
    }

    // 3. logback Logger
    if (fqn == "ch.qos.logback.classic.Logger") {
        std::string logbackSourcesJar = homeStr + "/.m2/repository/ch/qos/logback/logback-classic/1.5.6/logback-classic-1.5.6-sources.jar";
        if (fs::fileExists(logbackSourcesJar)) {
            std::string outPath = cacheDir_ + "/logback-classic/ch/qos/logback/classic/Logger.java";
            if (fs::fileExists(outPath)) return outPath;
            if (fs::extractZipEntry(logbackSourcesJar, "ch/qos/logback/classic/Logger.java", outPath)) {
                return outPath;
            }
        }
    }

    return std::nullopt;
}

} // namespace javalsp
