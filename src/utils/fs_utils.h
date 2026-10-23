#pragma once

#include <string>
#include <vector>

namespace javalsp {
namespace fs {

std::string readFile(const std::string& path);
bool writeFile(const std::string& path, const std::string& content);
bool fileExists(const std::string& path);
bool dirExists(const std::string& path);
bool ensureDir(const std::string& path);
std::string normalizePath(const std::string& path);
std::string uriToPath(const std::string& uri);
std::string pathToUri(const std::string& path);
std::vector<std::string> listFilesRecursively(const std::string& dir, const std::string& ext = ".java");
std::string runCommand(const std::string& cmd, const std::string& cwd = "");
bool extractZipEntry(const std::string& zipPath, const std::string& entryName, const std::string& outPath);

} // namespace fs
} // namespace javalsp
