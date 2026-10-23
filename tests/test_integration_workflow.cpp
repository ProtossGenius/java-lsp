#include "test_framework.h"
#include "lsp/server.h"
#include "lsp/transport.h"
#include "project/project_manager.h"
#include "utils/fs_utils.h"
#include <sstream>

using namespace javalsp;

namespace {

static std::optional<llvm::json::Value> parseResponse(std::stringstream& out) {
    std::string line;
    int contentLength = -1;
    while (std::getline(out, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.rfind("Content-Length: ", 0) == 0) {
            contentLength = std::stoi(line.substr(16));
        }
        if (line.empty()) {
            break;
        }
    }
    if (contentLength <= 0) return std::nullopt;

    std::vector<char> buf(contentLength);
    out.read(buf.data(), contentLength);
    llvm::Expected<llvm::json::Value> val = llvm::json::parse(std::string(buf.data(), contentLength));
    if (!val) {
        llvm::consumeError(val.takeError());
        return std::nullopt;
    }
    return std::move(*val);
}

} // namespace

TEST(IntegrationWorkflowTest, EndToEndClassRenameExtractMethodAndTypoFix) {
    std::stringstream in;
    std::stringstream out;
    Transport transport(in, out);
    Server server(transport);

    // Step 1: Build a class Greeter in Greeter.java
    std::string testDir = "/tmp/workflow_demo";
    fs::ensureDir(testDir);
    std::string oldUri = "file://" + testDir + "/Greeter.java";
    std::string newUri = "file://" + testDir + "/BetterGreeter.java";

    std::string initialSrc =
        "package com.sample;\n"
        "public class Greeter {\n"
        "    public void sayHello(String name) {\n"
        "        String greeting = \"Hello, \" + name;\n"
        "        System.out.println(greeting);\n"
        "    }\n"
        "}\n";

    fs::writeFile(testDir + "/Greeter.java", initialSrc);

    llvm::json::Object openParams{
        {"textDocument", llvm::json::Object{
            {"uri", oldUri},
            {"languageId", "java"},
            {"version", 1},
            {"text", initialSrc}
        }}
    };
    server.handleMessage(llvm::json::Value(llvm::json::Object{
        {"jsonrpc", "2.0"}, {"method", "textDocument/didOpen"}, {"params", std::move(openParams)}
    }));
    parseResponse(out); // discard diagnostics

    auto& pm = ProjectManager::instance();
    auto cu = pm.getCompilationUnit(oldUri);
    ASSERT_TRUE(cu != nullptr);
    ASSERT_EQ(cu->classes.size(), 1);
    ASSERT_EQ(cu->classes[0].name, "Greeter");

    // Step 2: Rename class Greeter to BetterGreeter -> must rename file and clear old cache
    llvm::json::Object renameReq{
        {"jsonrpc", "2.0"},
        {"id", 100},
        {"method", "textDocument/rename"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", oldUri}}},
            {"position", llvm::json::Object{{"line", 1}, {"character", 15}}}, // on "Greeter"
            {"newName", "BetterGreeter"}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(renameReq)));
    auto renameResp = parseResponse(out);
    ASSERT_TRUE(renameResp.has_value());
    auto renameObj = renameResp->getAsObject()->getObject("result");
    ASSERT_TRUE(renameObj != nullptr);

    // Verify cache in ProjectManager: old file cache must be deleted and new file registered!
    auto oldCu = pm.getCompilationUnit(oldUri);
    ASSERT_TRUE(oldCu == nullptr); // Old cache deleted!

    auto newCu = pm.getCompilationUnit(newUri);
    ASSERT_TRUE(newCu != nullptr); // New file indexed!

    // Step 3: Extract code block into a method
    std::string renamedSrc =
        "package com.sample;\n"
        "public class BetterGreeter {\n"
        "    public void sayHello(String name) {\n"
        "        String greeting = \"Hello, \" + name;\n"
        "        System.out.println(greeting);\n"
        "    }\n"
        "}\n";
    pm.updateFile(newUri, renamedSrc);

    // Request extract method on lines 3-4 (method body statements)
    llvm::json::Object refactorReq{
        {"jsonrpc", "2.0"},
        {"id", 101},
        {"method", "java/getRefactorEdit"},
        {"params", llvm::json::Object{
            {"command", "extractMethod"},
            {"context", llvm::json::Object{
                {"textDocument", llvm::json::Object{{"uri", newUri}}},
                {"range", llvm::json::Object{
                    {"start", llvm::json::Object{{"line", 3}, {"character", 8}}},
                    {"end", llvm::json::Object{{"line", 4}, {"character", 37}}}
                }}
            }}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(refactorReq)));
    auto refactorResp = parseResponse(out);
    ASSERT_TRUE(refactorResp.has_value());
    auto refactorObj = refactorResp->getAsObject()->getObject("result");
    ASSERT_TRUE(refactorObj != nullptr);
    auto changes = refactorObj->getObject("changes");
    ASSERT_TRUE(changes != nullptr);
    auto fileEdits = changes->getArray(newUri);
    ASSERT_TRUE(fileEdits != nullptr);
    ASSERT_EQ(fileEdits->size(), 2); // 1 for method call, 1 for method declaration!

    // Step 4 & 5: Intentional misspelling of method -> verify QuickFix code action
    std::string typoSrc =
        "package com.sample;\n"
        "public class BetterGreeter {\n"
        "    public void sayHello(String name) {\n"
        "        System.out.println(name);\n"
        "    }\n"
        "    public void test() {\n"
        "        sayHelo(\"world\");\n"
        "    }\n"
        "}\n";
    pm.updateFile(newUri, typoSrc);

    llvm::json::Object codeActionReq{
        {"jsonrpc", "2.0"},
        {"id", 102},
        {"method", "textDocument/codeAction"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", newUri}}},
            {"range", llvm::json::Object{
                {"start", llvm::json::Object{{"line", 6}, {"character", 8}}},
                {"end", llvm::json::Object{{"line", 6}, {"character", 15}}}
            }},
            {"context", llvm::json::Object{
                {"diagnostics", llvm::json::Array{
                    llvm::json::Object{
                        {"range", llvm::json::Object{
                            {"start", llvm::json::Object{{"line", 6}, {"character", 8}}},
                            {"end", llvm::json::Object{{"line", 6}, {"character", 15}}}
                        }},
                        {"message", "Method 'sayHelo' not found"},
                        {"severity", 1}
                    }
                }}
            }}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(codeActionReq)));
    auto caResp = parseResponse(out);
    ASSERT_TRUE(caResp.has_value());
    auto caArr = caResp->getAsObject()->getArray("result");
    ASSERT_TRUE(caArr != nullptr);

    bool foundQuickFix = false;
    for (const auto& actionVal : *caArr) {
        auto act = actionVal.getAsObject();
        if (act) {
            std::string title = act->getString("title").value_or("").str();
            if (title == "Change to 'sayHello'") {
                foundQuickFix = true;
                auto edit = act->getObject("edit");
                ASSERT_TRUE(edit != nullptr);
                break;
            }
        }
    }
    ASSERT_TRUE(foundQuickFix);
}
