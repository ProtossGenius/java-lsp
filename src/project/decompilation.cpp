#include "project/decompilation.h"
#include "semantics/symbol_table.h"
#include "utils/fs_utils.h"
#include "utils/logger.h"
#include <cstdlib>
#include <algorithm>
#include <unordered_set>

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

std::optional<std::string> DecompilationService::resolveSourceForFqn(const std::string& fqn, const SymbolTable* symTab) {
    if (!symTab) {
        symTab = &SymbolTable::instance();
    }

    // Primitives and keywords have no source file
    static const std::unordered_set<std::string> primitives = {
        "void", "int", "boolean", "byte", "char", "short", "long", "float", "double"
    };
    if (primitives.count(fqn)) return std::nullopt;

    // 1. JDK standard library
    if (fqn.rfind("java.", 0) == 0 || fqn.rfind("javax.", 0) == 0 || fqn.rfind("jdk.", 0) == 0 || fqn.rfind("sun.", 0) == 0) {
        auto jdk = getJdkSource(fqn);
        if (jdk) return jdk;
    }

    if (fqn.find('.') == std::string::npos) {
        auto langJdk = getJdkSource("java.lang." + fqn);
        if (langJdk) return langJdk;
        auto utilJdk = getJdkSource("java.util." + fqn);
        if (utilJdk) return utilJdk;
    }

    // 2. Look up class in SymbolTable
    const JavaClass* cls = symTab->findClassByFqn(fqn);
    if (!cls && fqn.find('.') == std::string::npos) {
        cls = symTab->findClassByName(fqn);
    }

    if (cls && !cls->sourceFilePath.empty()) {
        std::string archive = cls->sourceFilePath;
        if (fs::fileExists(archive)) {
            // Check if archive is src.zip
            if (archive.find("src.zip") != std::string::npos) {
                return getJdkSource(cls->fqn);
            }

            std::string relJava = cls->fqn;
            std::replace(relJava.begin(), relJava.end(), '.', '/');
            relJava += ".java";

            // Check if a -sources.jar exists next to this jar
            if (archive.size() > 4 && archive.substr(archive.size() - 4) == ".jar") {
                std::string sourcesJar = archive.substr(0, archive.size() - 4) + "-sources.jar";
                if (fs::fileExists(sourcesJar)) {
                    std::string outSrc = cacheDir_ + "/sources/" + relJava;
                    if (fs::fileExists(outSrc)) return outSrc;
                    if (fs::extractZipEntry(sourcesJar, relJava, outSrc)) {
                        return outSrc;
                    }
                }
            }

            // If not found in sources jar, extract .class and decompile with CFR
            std::string relClass = cls->fqn;
            std::replace(relClass.begin(), relClass.end(), '.', '/');
            relClass += ".class";

            std::string decompiledPath = cacheDir_ + "/decompiled/" + relJava;
            if (fs::fileExists(decompiledPath)) {
                return decompiledPath;
            }

            std::string classOut = cacheDir_ + "/classes/" + relClass;
            if (fs::extractZipEntry(archive, relClass, classOut)) {
                if (cfrJarPath_.empty()) locateCfrJar();
                if (!cfrJarPath_.empty()) {
                    std::string outDir = cacheDir_ + "/decompiled";
                    std::string cmd = "java -jar \"" + cfrJarPath_ + "\" \"" + classOut + "\" --outputdir \"" + outDir + "\" >/dev/null 2>&1";
                    fs::runCommand(cmd);
                    if (fs::fileExists(decompiledPath)) {
                        return decompiledPath;
                    }
                }
            }
        }
    }

    const char* home = std::getenv("HOME");
    std::string homeStr = home ? home : "";

    // 3. Fallback for common jars if not directly indexed
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
