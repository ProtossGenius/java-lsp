#include "project/dependency_resolver.h"
#include "project/jar_indexer.h"
#include "utils/fs_utils.h"
#include "utils/logger.h"

#include <cstdlib>
#include <array>
#include <memory>
#include <fstream>
#include <sstream>
#include <regex>

namespace javalsp {

static std::string execCommand(const std::string& cmd) {
    std::string fullCmd = "export PATH=\"/opt/homebrew/bin:/usr/local/bin:$PATH\" && " + cmd;
    std::array<char, 256> buffer;
    std::string result;
    std::unique_ptr<FILE, decltype(&pclose)> pipe(popen(fullCmd.c_str(), "r"), pclose);
    if (!pipe) return "";
    while (fgets(buffer.data(), buffer.size(), pipe.get()) != nullptr) {
        result += buffer.data();
    }
    while (!result.empty() && (result.back() == '\n' || result.back() == '\r' || result.back() == ' ')) {
        result.pop_back();
    }
    return result;
}

DependencyResolver& DependencyResolver::instance() {
    static DependencyResolver inst;
    return inst;
}

std::string DependencyResolver::findJavaHome() {
    const char* envHome = std::getenv("JAVA_HOME");
    if (envHome && fs::dirExists(envHome)) {
        return envHome;
    }

#ifdef __APPLE__
    std::string jh = execCommand("/usr/libexec/java_home 2>/dev/null");
    if (!jh.empty() && fs::dirExists(jh)) {
        return jh;
    }
#endif

    std::vector<std::string> candidates = {
        "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home",
        "/opt/homebrew/opt/openjdk",
        "/usr/local/opt/openjdk",
        "/usr/lib/jvm/default-java",
        "/usr/lib/jvm/java-17-openjdk"
    };

    for (const auto& c : candidates) {
        if (fs::dirExists(c)) return c;
    }

    return "";
}

void DependencyResolver::resolveAll(const std::string& workspaceRoot, SymbolTable& symTab) {
    if (!jdkResolved_) {
        resolveJdk(symTab);
        jdkResolved_ = true;
    }

    resolveM2Repository(symTab);

    if (!workspaceRoot.empty()) {
        resolveClasspathFiles(workspaceRoot, symTab);
        resolveMaven(workspaceRoot, symTab);
        resolveGradle(workspaceRoot, symTab);
    }
}

void DependencyResolver::resolveM2Repository(SymbolTable& symTab) {
    const char* home = std::getenv("HOME");
    if (!home) return;
    std::string m2 = std::string(home) + "/.m2/repository";
    if (!fs::dirExists(m2)) return;

    // Check p2 OSGi bundles if present (for Eclipse Tycho projects like jdtls / jdt.core)
    std::string p2BundleDir = m2 + "/p2/osgi/bundle";
    if (fs::dirExists(p2BundleDir)) {
        auto jars = fs::listFilesRecursively(p2BundleDir, ".jar");
        for (const auto& j : jars) {
            if (j.find(".source") == std::string::npos && j.find("-sources") == std::string::npos) {
                indexJar(j, symTab);
            }
        }
    }

    // Check JUnit 4 jar
    std::string junit4 = m2 + "/junit/junit/4.13.1/junit-4.13.1.jar";
    if (fs::fileExists(junit4)) {
        indexJar(junit4, symTab);
    } else {
        std::string junit4_12 = m2 + "/junit/junit/4.12/junit-4.12.jar";
        if (fs::fileExists(junit4_12)) indexJar(junit4_12, symTab);
    }
}

void DependencyResolver::indexJar(const std::string& jarPath, SymbolTable& symTab) {
    if (jarPath.empty() || indexedJars_.count(jarPath) || !fs::fileExists(jarPath)) {
        return;
    }
    indexedJars_.insert(jarPath);
    jarPaths_.push_back(jarPath);

    LOG_INFO("Indexing archive: " + jarPath);
    auto classes = JarIndexer::indexArchive(jarPath);
    for (const auto& cls : classes) {
        symTab.addClass(cls);
    }
    LOG_INFO("Indexed " + std::to_string(classes.size()) + " classes from " + jarPath);
}

void DependencyResolver::resolveJdk(SymbolTable& symTab) {
    std::string jh = findJavaHome();
    if (jh.empty()) {
        LOG_WARN("Could not determine JAVA_HOME for JDK indexing");
        return;
    }

    LOG_INFO("Resolved JAVA_HOME: " + jh);

    // Look for src.zip
    std::string srcZip = jh + "/lib/src.zip";
    if (fs::fileExists(srcZip)) {
        indexJar(srcZip, symTab);
        return;
    }

    // Look for jmods
    std::string jmodsDir = jh + "/jmods";
    if (fs::dirExists(jmodsDir)) {
        std::vector<std::string> jmods = {
            "java.base.jmod",
            "java.desktop.jmod",
            "java.sql.jmod",
            "java.xml.jmod",
            "java.net.http.jmod",
            "java.logging.jmod",
            "java.compiler.jmod"
        };
        for (const auto& jmod : jmods) {
            std::string p = jmodsDir + "/" + jmod;
            if (fs::fileExists(p)) {
                indexJar(p, symTab);
            }
        }
    }
}

void DependencyResolver::resolveClasspathFiles(const std::string& rootPath, SymbolTable& symTab) {
    auto files = fs::listFilesRecursively(rootPath, ".classpath");
    std::regex libRegex(R"raw(<classpathentry\s+[^>]*kind="lib"\s+[^>]*path="([^"]+)")raw");

    for (const auto& cpFile : files) {
        std::string content = fs::readFile(cpFile);
        std::sregex_iterator cur(content.begin(), content.end(), libRegex);
        std::sregex_iterator end;
        while (cur != end) {
            std::string jarPath = (*cur)[1].str();
            if (!jarPath.empty() && jarPath.front() != '/') {
                std::string dir = cpFile.substr(0, cpFile.find_last_of('/'));
                jarPath = dir + "/" + jarPath;
            }
            indexJar(jarPath, symTab);
            ++cur;
        }
    }
}

void DependencyResolver::resolveMaven(const std::string& rootPath, SymbolTable& symTab) {
    auto pomFiles = fs::listFilesRecursively(rootPath, "pom.xml");
    if (pomFiles.empty()) return;

    for (const auto& pom : pomFiles) {
        size_t lastSlash = pom.find_last_of('/');
        std::string dir = (lastSlash != std::string::npos) ? pom.substr(0, lastSlash) : rootPath;

        // Skip test fixture directories inside test resources to avoid launching dozens of mock maven processes
        if (dir.find("/src/test/resources/") != std::string::npos ||
            dir.find("/test-fixtures/") != std::string::npos ||
            dir.find("/testresources/") != std::string::npos) {
            continue;
        }

        std::string pomContent = fs::readFile(pom);
        if (pomContent.find("<packaging>pom</packaging>") != std::string::npos &&
            pomContent.find("<dependencies>") == std::string::npos) {
            continue;
        }
        if (pomContent.find("eclipse-plugin") != std::string::npos ||
            pomContent.find("eclipse-test-plugin") != std::string::npos ||
            pomContent.find("tycho") != std::string::npos) {
            continue;
        }
        if (pomContent.find("<dependencies>") == std::string::npos) {
            continue;
        }

        // Check if pre-generated .classpath_m2.txt exists
        std::string cachedCp = dir + "/.classpath_m2.txt";
        std::string cpString;
        if (fs::fileExists(cachedCp)) {
            cpString = fs::readFile(cachedCp);
        } else {
            // Run mvn dependency:build-classpath
            std::string cmd = "cd \"" + dir + "\" && mvn dependency:build-classpath -Dmdep.outputFile=.classpath_m2.txt -q 2>/dev/null";
            execCommand(cmd);
            if (fs::fileExists(cachedCp)) {
                cpString = fs::readFile(cachedCp);
            }
        }

        if (!cpString.empty()) {
            std::stringstream ss(cpString);
            std::string jar;
            while (std::getline(ss, jar, ':')) {
                while (!jar.empty() && (jar.back() == '\n' || jar.back() == '\r' || jar.back() == ' ')) {
                    jar.pop_back();
                }
                while (!jar.empty() && (jar.front() == ' ')) {
                    jar.erase(jar.begin());
                }
                if (!jar.empty()) {
                    indexJar(jar, symTab);
                }
            }
        }

        // Also index submodule build output directories if present
        std::string targetClasses = dir + "/target/classes";
        if (fs::dirExists(targetClasses)) {
            auto classes = JarIndexer::indexDirectory(targetClasses);
            for (const auto& c : classes) {
                symTab.addClass(c);
            }
        }
    }
}

void DependencyResolver::resolveGradle(const std::string& rootPath, SymbolTable& symTab) {
    auto gradleFiles = fs::listFilesRecursively(rootPath, "build.gradle");
    if (gradleFiles.empty()) {
        gradleFiles = fs::listFilesRecursively(rootPath, "build.gradle.kts");
    }
    if (gradleFiles.empty()) return;

    for (const auto& gf : gradleFiles) {
        size_t lastSlash = gf.find_last_of('/');
        std::string dir = (lastSlash != std::string::npos) ? gf.substr(0, lastSlash) : rootPath;
        std::string buildClasses = dir + "/build/classes/java/main";
        if (fs::dirExists(buildClasses)) {
            auto classes = JarIndexer::indexDirectory(buildClasses);
            for (const auto& c : classes) {
                symTab.addClass(c);
            }
        }
    }
}

} // namespace javalsp
