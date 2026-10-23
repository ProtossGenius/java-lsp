#include "project/jar_indexer.h"
#include "utils/logger.h"
#include "utils/fs_utils.h"

#include <fstream>
#include <cstring>
#include <cstdint>
#include <algorithm>

namespace javalsp {

#pragma pack(push, 1)
struct ZipEOCD {
    uint32_t signature;
    uint16_t diskNumber;
    uint16_t startDisk;
    uint16_t numEntriesDisk;
    uint16_t numEntriesTotal;
    uint32_t sizeCentralDir;
    uint32_t offsetCentralDir;
    uint16_t commentLength;
};
#pragma pack(pop)

std::vector<std::string> JarIndexer::readZipEntries(const std::string& zipPath) {
    std::vector<std::string> entries;
    std::ifstream file(zipPath, std::ios::binary | std::ios::ate);
    if (!file.is_open()) return entries;

    std::streampos fileSize = file.tellg();
    if (fileSize < 22) return entries;

    size_t searchLen = std::min(static_cast<size_t>(fileSize), static_cast<size_t>(65557));
    file.seekg(-static_cast<std::streamoff>(searchLen), std::ios::end);

    std::vector<char> buffer(searchLen);
    file.read(buffer.data(), searchLen);

    // Look for PK\5\6
    int64_t eocdOffsetInBuffer = -1;
    for (int64_t i = static_cast<int64_t>(searchLen) - 22; i >= 0; --i) {
        if (buffer[i] == 0x50 && buffer[i + 1] == 0x4b &&
            buffer[i + 2] == 0x05 && buffer[i + 3] == 0x06) {
            eocdOffsetInBuffer = i;
            break;
        }
    }

    if (eocdOffsetInBuffer < 0) return entries;

    ZipEOCD eocd;
    std::memcpy(&eocd, buffer.data() + eocdOffsetInBuffer, sizeof(ZipEOCD));

    if (eocd.offsetCentralDir >= fileSize) return entries;

    file.seekg(eocd.offsetCentralDir, std::ios::beg);
    std::vector<char> cdBuffer(eocd.sizeCentralDir);
    file.read(cdBuffer.data(), eocd.sizeCentralDir);

    size_t p = 0;
    while (p + 46 <= cdBuffer.size()) {
        if (static_cast<uint8_t>(cdBuffer[p]) != 0x50 ||
            static_cast<uint8_t>(cdBuffer[p + 1]) != 0x4b ||
            static_cast<uint8_t>(cdBuffer[p + 2]) != 0x01 ||
            static_cast<uint8_t>(cdBuffer[p + 3]) != 0x02) {
            break;
        }

        uint16_t fnameLen = *reinterpret_cast<const uint16_t*>(&cdBuffer[p + 28]);
        uint16_t extraLen = *reinterpret_cast<const uint16_t*>(&cdBuffer[p + 30]);
        uint16_t commentLen = *reinterpret_cast<const uint16_t*>(&cdBuffer[p + 32]);

        if (p + 46 + fnameLen <= cdBuffer.size()) {
            std::string fname(&cdBuffer[p + 46], fnameLen);
            entries.push_back(fname);
        }

        p += 46 + fnameLen + extraLen + commentLen;
    }

    return entries;
}

std::vector<JavaClass> JarIndexer::indexArchive(const std::string& archivePath) {
    std::vector<JavaClass> classes;
    auto entries = readZipEntries(archivePath);

    for (std::string entry : entries) {
        bool isClass = false;
        bool isJava = false;

        if (entry.size() > 6 && entry.substr(entry.size() - 6) == ".class") {
            isClass = true;
            entry = entry.substr(0, entry.size() - 6);
        } else if (entry.size() > 5 && entry.substr(entry.size() - 5) == ".java") {
            isJava = true;
            entry = entry.substr(0, entry.size() - 5);
        } else {
            continue;
        }

        // Handle jmod "classes/" prefix
        if (entry.rfind("classes/", 0) == 0) {
            entry = entry.substr(8);
        }

        // Handle src.zip module prefix e.g. "java.base/"
        size_t firstSlash = entry.find('/');
        if (firstSlash != std::string::npos) {
            std::string prefix = entry.substr(0, firstSlash);
            if (prefix == "java.base" || prefix == "java.desktop" ||
                prefix == "java.sql" || prefix == "java.xml" ||
                prefix == "java.net.http" || prefix == "java.logging" ||
                prefix == "java.compiler" || prefix == "java.instrument" ||
                prefix.rfind("jdk.", 0) == 0) {
                entry = entry.substr(firstSlash + 1);
            }
        }

        // Skip module-info and package-info
        if (entry == "module-info" || entry == "package-info" ||
            entry.size() > 12 && entry.substr(entry.size() - 12) == "/module-info" ||
            entry.size() > 13 && entry.substr(entry.size() - 13) == "/package-info") {
            continue;
        }

        // Skip META-INF
        if (entry.rfind("META-INF", 0) == 0) {
            continue;
        }

        // Inner class: Foo$Bar or Foo$1
        size_t dollarPos = entry.find('$');
        if (dollarPos != std::string::npos) {
            std::string afterDollar = entry.substr(dollarPos + 1);
            // Skip anonymous inner classes like Foo$1, Foo$2
            if (!afterDollar.empty() && std::all_of(afterDollar.begin(), afterDollar.end(), ::isdigit)) {
                continue;
            }
        }

        // Convert slashes to dots
        std::string fqn = entry;
        std::replace(fqn.begin(), fqn.end(), '/', '.');

        size_t lastDot = fqn.find_last_of('.');
        std::string pkg = "";
        std::string simpleName = fqn;
        if (lastDot != std::string::npos) {
            pkg = fqn.substr(0, lastDot);
            simpleName = fqn.substr(lastDot + 1);
        }

        // If inner class with $, simpleName could be Foo$Bar -> Bar
        if (simpleName.find('$') != std::string::npos) {
            size_t innerDollar = simpleName.find_last_of('$');
            simpleName = simpleName.substr(innerDollar + 1);
        }

        JavaClass cls;
        cls.name = simpleName;
        cls.packageName = pkg;
        cls.fqn = fqn;
        cls.kind = ClassKind::CLASS;
        classes.push_back(cls);
    }

    return classes;
}

std::vector<JavaClass> JarIndexer::indexDirectory(const std::string& dirPath) {
    std::vector<JavaClass> classes;
    auto classFiles = fs::listFilesRecursively(dirPath, ".class");
    std::string normDir = fs::normalizePath(dirPath);

    for (const auto& f : classFiles) {
        std::string normF = fs::normalizePath(f);
        if (normF.rfind(normDir, 0) == 0) {
            std::string rel = normF.substr(normDir.size());
            if (!rel.empty() && rel.front() == '/') rel = rel.substr(1);
            if (rel.size() > 6 && rel.substr(rel.size() - 6) == ".class") {
                rel = rel.substr(0, rel.size() - 6);
                std::replace(rel.begin(), rel.end(), '/', '.');
                size_t lastDot = rel.find_last_of('.');
                JavaClass cls;
                cls.fqn = rel;
                if (lastDot != std::string::npos) {
                    cls.packageName = rel.substr(0, lastDot);
                    cls.name = rel.substr(lastDot + 1);
                } else {
                    cls.name = rel;
                }
                cls.kind = ClassKind::CLASS;
                classes.push_back(cls);
            }
        }
    }
    return classes;
}

} // namespace javalsp
