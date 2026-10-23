#include "project/project_manager.h"
#include "project/dependency_resolver.h"
#include "semantics/symbol_table.h"
#include "semantics/type_checker.h"
#include "completion/completion_engine.h"
#include "parser/tree_sitter_loader.h"
#include "utils/fs_utils.h"
#include "utils/logger.h"

#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <regex>
#include <cctype>
#include <iomanip>
#include <cstdlib>

using namespace javalsp;

struct PTestResult {
    std::string filePath;
    int totalTokens = 0;
    int completedTokens = 0;
    int missedTokens = 0;
    std::vector<std::string> missedTokenNames;
    int javacErrors = 0;
    int lspErrors = 0;
    std::vector<std::string> javacErrorMsgs;
    std::vector<std::string> lspErrorMsgs;
    bool simulationSuccess = true;
    bool diagnosticsMatch = true;
};

// Check if a character or token is a constant / delimiter
static bool isConstantOrDelimiter(const std::string& token) {
    if (token.empty()) return true;
    // Check if whitespace
    if (std::all_of(token.begin(), token.end(), [](unsigned char ch) { return std::isspace(ch); })) {
        return true;
    }
    // Check if punctuation / operators (except @ which can start annotations)
    if (std::all_of(token.begin(), token.end(), [](unsigned char ch) { return std::ispunct(ch) && ch != '_' && ch != '@'; })) {
        return true;
    }
    // Check if numeric literal
    if (std::isdigit(static_cast<unsigned char>(token[0]))) return true;
    // Check if string or char literal
    if (token.front() == '"' || token.front() == '\'') return true;
    // Check if boolean / null literal
    if (token == "true" || token == "false" || token == "null") return true;
    // Check if comment
    if (token.rfind("//", 0) == 0 || token.rfind("/*", 0) == 0) return true;

    return false;
}

// Tokenize Java source code into tokens (preserving comments, literals, punctuation, and identifiers)
static std::vector<std::string> tokenizeSource(const std::string& src) {
    std::vector<std::string> tokens;
    size_t i = 0;
    size_t n = src.size();

    while (i < n) {
        char c = src[i];

        // Whitespace
        if (std::isspace(c)) {
            size_t start = i;
            while (i < n && std::isspace(src[i])) i++;
            tokens.push_back(src.substr(start, i - start));
            continue;
        }

        // Single-line comment
        if (c == '/' && i + 1 < n && src[i + 1] == '/') {
            size_t start = i;
            while (i < n && src[i] != '\n') i++;
            tokens.push_back(src.substr(start, i - start));
            continue;
        }

        // Multi-line comment
        if (c == '/' && i + 1 < n && src[i + 1] == '*') {
            size_t start = i;
            i += 2;
            while (i + 1 < n && !(src[i] == '*' && src[i + 1] == '/')) i++;
            if (i + 1 < n) i += 2;
            tokens.push_back(src.substr(start, i - start));
            continue;
        }

        // String literal
        if (c == '"') {
            size_t start = i;
            i++;
            while (i < n && src[i] != '"') {
                if (src[i] == '\\' && i + 1 < n) i++;
                i++;
            }
            if (i < n) i++;
            tokens.push_back(src.substr(start, i - start));
            continue;
        }

        // Char literal
        if (c == '\'') {
            size_t start = i;
            i++;
            while (i < n && src[i] != '\'') {
                if (src[i] == '\\' && i + 1 < n) i++;
                i++;
            }
            if (i < n) i++;
            tokens.push_back(src.substr(start, i - start));
            continue;
        }

        // Identifiers / keywords / numbers
        if (std::isalnum(c) || c == '_') {
            size_t start = i;
            while (i < n && (std::isalnum(src[i]) || src[i] == '_')) i++;
            tokens.push_back(src.substr(start, i - start));
            continue;
        }

        // Other punctuation
        tokens.push_back(std::string(1, c));
        i++;
    }

    return tokens;
}

// Separate header (package, imports, class Foo {) from body using tree-sitter
static bool partitionJavaFile(
    const std::string& src,
    std::string& header,
    std::vector<std::string>& bodyTokens,
    std::string& footer) {

    TSParser* parser = TreeSitterLoader::instance().newParser();
    if (!parser) return false;

    TSTree* tree = ts_parser_parse_string(parser, nullptr, src.c_str(), src.size());
    if (!tree) {
        ts_parser_delete(parser);
        return false;
    }

    TSNode root = ts_tree_root_node(tree);
    uint32_t childCount = ts_node_child_count(root);

    TSNode targetClass{};
    for (uint32_t i = 0; i < childCount; ++i) {
        TSNode child = ts_node_child(root, i);
        std::string type = ts_node_type(child);
        if (type == "class_declaration" || type == "interface_declaration" ||
            type == "enum_declaration" || type == "record_declaration" ||
            type == "annotation_type_declaration") {
            targetClass = child;
            break;
        }
    }

    if (ts_node_is_null(targetClass)) {
        ts_tree_delete(tree);
        ts_parser_delete(parser);
        return false;
    }

    TSNode bodyNode = ts_node_child_by_field_name(targetClass, "body", 4);
    if (ts_node_is_null(bodyNode)) {
        for (uint32_t i = 0; i < ts_node_child_count(targetClass); ++i) {
            TSNode c = ts_node_child(targetClass, i);
            std::string ct = ts_node_type(c);
            if (ct.find("body") != std::string::npos) {
                bodyNode = c;
                break;
            }
        }
    }

    if (ts_node_is_null(bodyNode)) {
        ts_tree_delete(tree);
        ts_parser_delete(parser);
        return false;
    }

    uint32_t bodyStart = ts_node_start_byte(bodyNode);
    uint32_t bodyEnd = ts_node_end_byte(bodyNode);

    if (bodyStart >= bodyEnd || bodyEnd > src.size()) {
        ts_tree_delete(tree);
        ts_parser_delete(parser);
        return false;
    }

    // Opening brace is at bodyStart
    size_t headerEnd = bodyStart + 1; // includes '{'
    size_t footerStart = (bodyEnd > 0) ? bodyEnd - 1 : bodyEnd; // starts at '}'

    header = src.substr(0, headerEnd);
    std::string body = src.substr(headerEnd, footerStart - headerEnd);
    footer = src.substr(footerStart);

    bodyTokens = tokenizeSource(body);

    ts_tree_delete(tree);
    ts_parser_delete(parser);
    return true;
}

// Compute line and character position from string offset
static Position getPositionFromOffset(const std::string& text, size_t offset) {
    Position pos{0, 0};
    for (size_t i = 0; i < offset && i < text.size(); ++i) {
        if (text[i] == '\n') {
            pos.line++;
            pos.character = 0;
        } else {
            pos.character++;
        }
    }
    return pos;
}

// Run compiler once to collect errors across the project
static std::unordered_map<std::string, std::vector<std::string>> runProjectCompiler(const std::string& projectRoot) {
    std::unordered_map<std::string, std::vector<std::string>> errors;
    std::string pomPath = projectRoot + "/pom.xml";
    if (fs::fileExists(pomPath)) {
        std::string pomContent = fs::readFile(pomPath);
        if (pomContent.find("tycho") != std::string::npos || pomContent.find("eclipse-plugin") != std::string::npos) {
            // Tycho projects use OSGi / target platforms; skip full reactor compile
            return errors;
        }
        std::string cmd = "cd \"" + projectRoot + "\" && mvn compile -q 2>&1";
        std::array<char, 256> buffer;
        std::string result;
        std::unique_ptr<FILE, decltype(&pclose)> pipe(popen(cmd.c_str(), "r"), pclose);
        if (pipe) {
            while (fgets(buffer.data(), buffer.size(), pipe.get()) != nullptr) {
                result += buffer.data();
            }
        }
        std::istringstream ss(result);
        std::string line;
        while (std::getline(ss, line)) {
            if (line.find("[ERROR]") != std::string::npos) {
                size_t colon = line.find(".java:[");
                if (colon == std::string::npos) colon = line.find(".java:");
                if (colon != std::string::npos) {
                    size_t start = line.find("/");
                    if (start != std::string::npos && start < colon) {
                        std::string file = line.substr(start, (colon + 5) - start);
                        errors[file].push_back(line);
                    }
                }
            }
        }
    }
    return errors;
}

// Run the character-by-character simulation test on a single Java file
static PTestResult testJavaFile(
    const std::string& filePath,
    const std::string& projectRoot,
    ProjectManager& pm,
    CompletionEngine& completionEngine,
    const std::unordered_map<std::string, std::vector<std::string>>& projectCompilerErrors) {

    PTestResult res;
    res.filePath = filePath;

    std::string src = fs::readFile(filePath);
    if (src.empty()) {
        res.simulationSuccess = false;
        return res;
    }

    std::string header;
    std::vector<std::string> bodyTokens;
    std::string footer;

    if (!partitionJavaFile(src, header, bodyTokens, footer)) {
        // Entire file test fallback
        bodyTokens = tokenizeSource(src);
        header = "";
        footer = "";
    }

    std::string uri = fs::pathToUri(filePath);
    std::string currentText = header;

    // Simulate typing token by token
    for (size_t idx = 0; idx < bodyTokens.size(); ++idx) {
        const std::string& token = bodyTokens[idx];

        if (isConstantOrDelimiter(token)) {
            // Constant part: directly append
            currentText += token;
        } else {
            // Non-constant part: simulate typing character by character
            res.totalTokens++;
            bool matched = false;

            for (size_t c = 1; c <= token.size(); ++c) {
                std::string typed = token.substr(0, c);
                std::string partialDoc = currentText + typed + footer;
                Position partialPos = getPositionFromOffset(currentText + typed, (currentText + typed).size());
                pm.updateFile(uri, partialDoc);

                auto cu = pm.getCompilationUnit(uri);
                auto scope = pm.getFileScope(uri);
                if (cu) {
                    auto list = completionEngine.complete(*cu, partialDoc, partialPos, scope);
                    for (const auto& item : list.items) {
                        std::string target = token;
                        if (!target.empty() && target.front() == '@') target = target.substr(1);
                        if (item.label == target || item.label == token || item.insertText == token || item.insertText == target) {
                            matched = true;
                            break;
                        }
                        if (item.label.size() >= target.size() && item.label.rfind(target, 0) == 0) {
                            if (item.label.size() == target.size() || item.label[target.size()] == '(' || item.label[target.size()] == '.') {
                                matched = true;
                                break;
                            }
                        }
                    }
                }
                if (matched) {
                    break;
                }
            }

            if (matched) {
                res.completedTokens++;
            } else {
                res.missedTokens++;
                res.missedTokenNames.push_back(token);
            }

            // Fill token
            currentText += token;
            pm.updateFile(uri, currentText + footer);
        }
    }

    // Append footer
    currentText += footer;
    pm.updateFile(uri, currentText);

    // Diagnostics comparison
    auto cu = pm.getCompilationUnit(uri);
    if (cu) {
        auto lspDiags = TypeChecker::check(*cu, currentText, TSNode{}, pm.getSymbolTable());
        res.lspErrors = static_cast<int>(lspDiags.size());
        for (const auto& d : lspDiags) {
            res.lspErrorMsgs.push_back("line " + std::to_string(d.range.start.line) + ": " + d.message);
        }
    }

    auto it = projectCompilerErrors.find(filePath);
    if (it != projectCompilerErrors.end()) {
        res.javacErrors = static_cast<int>(it->second.size());
        res.javacErrorMsgs = it->second;
    } else if (res.lspErrors > 0) {
        // If LSP reported errors and no project-wide compiler errors recorded, verify with javac directly using indexed classpath
        std::string cpStr = "";
        for (const auto& jar : DependencyResolver::instance().getDiscoveredJars()) {
            if (!cpStr.empty()) cpStr += ":";
            cpStr += jar;
        }
        std::string cmd;
        if (!cpStr.empty()) {
            cmd = "javac -proc:none -cp \"" + cpStr + "\" \"" + filePath + "\" 2>&1";
        } else {
            cmd = "javac -proc:none \"" + filePath + "\" 2>&1";
        }
        std::array<char, 256> buffer;
        std::string result;
        std::unique_ptr<FILE, decltype(&pclose)> pipe(popen(cmd.c_str(), "r"), pclose);
        if (pipe) {
            while (fgets(buffer.data(), buffer.size(), pipe.get()) != nullptr) {
                result += buffer.data();
            }
        }
        std::istringstream ss(result);
        std::string line;
        while (std::getline(ss, line)) {
            if (line.find("error:") != std::string::npos || line.find("错误:") != std::string::npos) {
                res.javacErrorMsgs.push_back(line);
            }
        }
        res.javacErrors = static_cast<int>(res.javacErrorMsgs.size());
    } else {
        res.javacErrors = 0;
    }

    if (res.javacErrors != res.lspErrors) {
        res.diagnosticsMatch = false;
    }

    return res;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cout << "Usage: ptest <path-to-java-project> [--file <specific-file>]" << std::endl;
        return 1;
    }

    std::string projectRoot = fs::normalizePath(argv[1]);
    std::string specificFile = "";
    int maxFiles = 0;

    for (int i = 2; i < argc; ++i) {
        if (std::string(argv[i]) == "--file" && i + 1 < argc) {
            specificFile = argv[++i];
        } else if (std::string(argv[i]) == "--max-files" && i + 1 < argc) {
            maxFiles = std::stoi(argv[++i]);
        }
    }

    std::cout << "========================================" << std::endl;
    std::cout << "  ptest - Java LSP Simulation Test Tool " << std::endl;
    std::cout << "========================================" << std::endl;
    std::cout << "Target Project: " << projectRoot << std::endl;

    auto& pm = ProjectManager::instance();
    pm.initWorkspace(projectRoot);

    auto& symTab = pm.getSymbolTable();
    CompletionEngine completionEngine(symTab);

    std::cout << "Compiling project to retrieve baseline compiler diagnostics..." << std::endl;
    auto projectCompilerErrors = runProjectCompiler(projectRoot);

    std::vector<std::string> javaFiles;
    if (!specificFile.empty()) {
        javaFiles.push_back(specificFile);
    } else {
        javaFiles = fs::listFilesRecursively(projectRoot, ".java");
    }

    if (maxFiles > 0 && javaFiles.size() > static_cast<size_t>(maxFiles)) {
        javaFiles.resize(maxFiles);
    }

    std::cout << "Found " << javaFiles.size() << " Java files to test." << std::endl << std::endl;

    int totalPassed = 0;
    int totalDiff = 0;
    int totalFailed = 0;

    for (size_t i = 0; i < javaFiles.size(); ++i) {
        const auto& f = javaFiles[i];
        size_t lastSlash = f.find_last_of('/');
        std::string fname = (lastSlash != std::string::npos) ? f.substr(lastSlash + 1) : f;

        std::cout << "[" << (i + 1) << "/" << javaFiles.size() << "] Testing " << fname << "... " << std::flush;

        auto res = testJavaFile(f, projectRoot, pm, completionEngine, projectCompilerErrors);

        if (res.diagnosticsMatch && res.missedTokens == 0) {
            std::cout << "PASS (Tokens: " << res.completedTokens << "/" << res.totalTokens
                      << ", Diags: " << res.lspErrors << ")" << std::endl;
            totalPassed++;
        } else if (!res.diagnosticsMatch) {
            std::cout << "DIFF (LSP errors: " << res.lspErrors << " vs Javac errors: " << res.javacErrors << ")" << std::endl;
            for (const auto& msg : res.lspErrorMsgs) {
                std::cout << "    [LSP] " << msg << std::endl;
            }
            for (const auto& msg : res.javacErrorMsgs) {
                std::cout << "    [JAVAC] " << msg << std::endl;
            }
            totalDiff++;
        } else {
            std::cout << "WARN (Completed: " << res.completedTokens << "/" << res.totalTokens << ")" << std::endl;
            totalPassed++;
        }
    }

    std::cout << "\n========================================" << std::endl;
    std::cout << "Summary: " << totalPassed << " Passed, " << totalDiff << " Diagnostic Differences, "
              << totalFailed << " Failed" << std::endl;
    std::cout << "========================================" << std::endl;

    return (totalDiff > 0 || totalFailed > 0) ? 1 : 0;
}
