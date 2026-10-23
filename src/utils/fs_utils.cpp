#include "utils/fs_utils.h"
#include "utils/logger.h"
#include <fstream>
#include <sstream>
#include <sys/stat.h>
#include <dirent.h>
#include <unistd.h>
#include <cstdio>
#include <array>
#include <memory>
#include <cctype>

namespace javalsp {
namespace fs {

static std::string urlDecode(const std::string& str) {
    std::string ret;
    ret.reserve(str.length());
    for (size_t i = 0; i < str.length(); ++i) {
        if (str[i] == '%' && i + 2 < str.length()) {
            char h1 = str[i + 1];
            char h2 = str[i + 2];
            int v1 = (h1 >= '0' && h1 <= '9') ? (h1 - '0') :
                     (h1 >= 'a' && h1 <= 'f') ? (h1 - 'a' + 10) :
                     (h1 >= 'A' && h1 <= 'F') ? (h1 - 'A' + 10) : -1;
            int v2 = (h2 >= '0' && h2 <= '9') ? (h2 - '0') :
                     (h2 >= 'a' && h2 <= 'f') ? (h2 - 'a' + 10) :
                     (h2 >= 'A' && h2 <= 'F') ? (h2 - 'A' + 10) : -1;
            if (v1 != -1 && v2 != -1) {
                ret.push_back(static_cast<char>((v1 << 4) | v2));
                i += 2;
                continue;
            }
        }
        ret.push_back(str[i]);
    }
    return ret;
}

std::string readFile(const std::string& path) {
    std::ifstream ifs(path, std::ios::in | std::ios::binary);
    if (!ifs.is_open()) return "";
    std::ostringstream ss;
    ss << ifs.rdbuf();
    return ss.str();
}

bool writeFile(const std::string& path, const std::string& content) {
    size_t lastSlash = path.find_last_of('/');
    if (lastSlash != std::string::npos) {
        ensureDir(path.substr(0, lastSlash));
    }
    std::ofstream ofs(path, std::ios::out | std::ios::binary | std::ios::trunc);
    if (!ofs.is_open()) return false;
    ofs << content;
    return ofs.good();
}

bool fileExists(const std::string& path) {
    struct stat st;
    if (stat(path.c_str(), &st) != 0) return false;
    return S_ISREG(st.st_mode);
}

bool dirExists(const std::string& path) {
    struct stat st;
    if (stat(path.c_str(), &st) != 0) return false;
    return S_ISDIR(st.st_mode);
}

bool ensureDir(const std::string& path) {
    if (path.empty()) return true;
    if (dirExists(path)) return true;

    size_t pos = 0;
    while ((pos = path.find_first_of('/', pos + 1)) != std::string::npos) {
        std::string sub = path.substr(0, pos);
        if (!sub.empty() && !dirExists(sub)) {
            mkdir(sub.c_str(), 0755);
        }
    }
    if (!dirExists(path)) {
        mkdir(path.c_str(), 0755);
    }
    return dirExists(path);
}

std::string normalizePath(const std::string& path) {
    if (path.empty()) return "";
    char resolved[PATH_MAX];
    if (realpath(path.c_str(), resolved) != nullptr) {
        return std::string(resolved);
    }
    return path;
}

std::string uriToPath(const std::string& uri) {
    std::string prefix = "file://";
    if (uri.rfind(prefix, 0) == 0) {
        std::string path = uri.substr(prefix.length());
        return urlDecode(path);
    }
    return uri;
}

std::string pathToUri(const std::string& path) {
    if (path.rfind("file://", 0) == 0) return path;
    std::string norm = normalizePath(path);
    return "file://" + norm;
}

std::vector<std::string> listFilesRecursively(const std::string& dir, const std::string& ext) {
    std::vector<std::string> results;
    DIR* dp = opendir(dir.c_str());
    if (!dp) return results;

    struct dirent* entry;
    while ((entry = readdir(dp)) != nullptr) {
        std::string name = entry->d_name;
        if (name == "." || name == "..") continue;

        std::string fullPath = dir + "/" + name;
        if (entry->d_type == DT_DIR) {
            auto sub = listFilesRecursively(fullPath, ext);
            results.insert(results.end(), sub.begin(), sub.end());
        } else if (entry->d_type == DT_REG) {
            if (ext.empty() || (fullPath.length() >= ext.length() &&
                fullPath.compare(fullPath.length() - ext.length(), ext.length(), ext) == 0)) {
                results.push_back(fullPath);
            }
        }
    }
    closedir(dp);
    return results;
}

std::string runCommand(const std::string& cmd, const std::string& cwd) {
    std::string fullCmd = cmd;
    if (!cwd.empty()) {
        fullCmd = "cd \"" + cwd + "\" && " + cmd;
    }
    fullCmd += " 2>&1";

    std::array<char, 256> buffer;
    std::string result;
    std::unique_ptr<FILE, decltype(&pclose)> pipe(popen(fullCmd.c_str(), "r"), pclose);
    if (!pipe) {
        return "";
    }
    while (fgets(buffer.data(), buffer.size(), pipe.get()) != nullptr) {
        result += buffer.data();
    }
    return result;
}

bool extractZipEntry(const std::string& zipPath, const std::string& entryName, const std::string& outPath) {
    if (!fileExists(zipPath)) return false;
    std::string cmd = "unzip -p \"" + zipPath + "\" \"" + entryName + "\"";
    std::string content = runCommand(cmd);
    if (content.empty()) return false;
    return writeFile(outPath, content);
}

} // namespace fs
} // namespace javalsp
