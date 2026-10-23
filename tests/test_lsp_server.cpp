#include "test_framework.h"
#include "lsp/server.h"
#include "lsp/transport.h"
#include "utils/fs_utils.h"
#include <sstream>
#include <llvm/Support/JSON.h>

using namespace javalsp;

namespace {

// Helper to extract JSON payload from Transport output (strips Content-Length header)
std::optional<llvm::json::Value> parseResponse(std::stringstream& out) {
    std::string text = out.str();
    out.str("");
    out.clear();

    size_t headerEnd = text.find("\r\n\r\n");
    if (headerEnd == std::string::npos) return std::nullopt;

    std::string jsonBody = text.substr(headerEnd + 4);
    auto parsed = llvm::json::parse(jsonBody);
    if (!parsed) return std::nullopt;
    return std::move(*parsed);
}

} // namespace

TEST(LspServerTest, InitializeAndShutdown) {
    std::stringstream in;
    std::stringstream out;
    Transport transport(in, out);
    Server server(transport);

    // 1. Initialize
    llvm::json::Object initParams{
        {"rootUri", "file:///tmp/dummy_project"},
        {"initializationOptions", llvm::json::Object{
            {"mysql", llvm::json::Object{
                {"host", "localhost"},
                {"port", 3306},
                {"user", "root"},
                {"database", "demo"}
            }}
        }}
    };
    llvm::json::Object initReq{
        {"jsonrpc", "2.0"},
        {"id", 1},
        {"method", "initialize"},
        {"params", std::move(initParams)}
    };

    server.handleMessage(llvm::json::Value(std::move(initReq)));
    auto respVal = parseResponse(out);
    ASSERT_TRUE(respVal.has_value());
    auto respObj = respVal->getAsObject();
    ASSERT_TRUE(respObj != nullptr);
    ASSERT_EQ(*respObj->getInteger("id"), 1);

    auto result = respObj->getObject("result");
    ASSERT_TRUE(result != nullptr);
    auto caps = result->getObject("capabilities");
    ASSERT_TRUE(caps != nullptr);

    // Verify all capabilities advertised
    ASSERT_TRUE(caps->getObject("completionProvider") != nullptr);
    ASSERT_TRUE(caps->getBoolean("definitionProvider").value_or(false));
    ASSERT_TRUE(caps->getBoolean("declarationProvider").value_or(false));
    ASSERT_TRUE(caps->getBoolean("implementationProvider").value_or(false));
    ASSERT_TRUE(caps->getBoolean("hoverProvider").value_or(false));
    ASSERT_TRUE(caps->getBoolean("documentSymbolProvider").value_or(false));
    ASSERT_TRUE(caps->getBoolean("documentFormattingProvider").value_or(false));
    ASSERT_TRUE(caps->getBoolean("codeActionProvider").value_or(false));

    auto renameProv = caps->getObject("renameProvider");
    ASSERT_TRUE(renameProv != nullptr);
    ASSERT_TRUE(renameProv->getBoolean("prepareProvider").value_or(false));

    // 2. Shutdown
    llvm::json::Object shutdownReq{
        {"jsonrpc", "2.0"},
        {"id", 2},
        {"method", "shutdown"}
    };
    server.handleMessage(llvm::json::Value(std::move(shutdownReq)));
    auto shutVal = parseResponse(out);
    ASSERT_TRUE(shutVal.has_value());
    auto shutObj = shutVal->getAsObject();
    ASSERT_TRUE(shutObj != nullptr);
    ASSERT_EQ(*shutObj->getInteger("id"), 2);
}

TEST(LspServerTest, DidOpenDidChangeDidCloseDiagnostics) {
    std::stringstream in;
    std::stringstream out;
    Transport transport(in, out);
    Server server(transport);

    std::string uri = "file:///tmp/SampleProbe.java";
    std::string src =
        "package com.sample;\n"
        "public class SampleProbe {\n"
        "    public void run() {\n"
        "        UnknownType missing = null;\n"
        "    }\n"
        "}\n";

    // 1. didOpen with unresolved type
    llvm::json::Object openParams{
        {"textDocument", llvm::json::Object{
            {"uri", uri},
            {"languageId", "java"},
            {"version", 1},
            {"text", src}
        }}
    };
    llvm::json::Object openNotif{
        {"jsonrpc", "2.0"},
        {"method", "textDocument/didOpen"},
        {"params", std::move(openParams)}
    };
    server.handleMessage(llvm::json::Value(std::move(openNotif)));

    auto diagVal = parseResponse(out);
    ASSERT_TRUE(diagVal.has_value());
    auto diagObj = diagVal->getAsObject();
    ASSERT_TRUE(diagObj != nullptr);
    ASSERT_EQ(std::string(diagObj->getString("method").value_or("")), "textDocument/publishDiagnostics");

    auto diagParams = diagObj->getObject("params");
    ASSERT_TRUE(diagParams != nullptr);
    auto diagsArr = diagParams->getArray("diagnostics");
    ASSERT_TRUE(diagsArr != nullptr);
    ASSERT_TRUE(!diagsArr->empty()); // UnknownType diagnosed

    // 2. didChange to valid code
    std::string fixedSrc =
        "package com.sample;\n"
        "public class SampleProbe {\n"
        "    public void run() {\n"
        "        String ok = \"hello\";\n"
        "    }\n"
        "}\n";

    llvm::json::Object changeParams{
        {"textDocument", llvm::json::Object{
            {"uri", uri},
            {"version", 2}
        }},
        {"contentChanges", llvm::json::Array{
            llvm::json::Object{{"text", fixedSrc}}
        }}
    };
    llvm::json::Object changeNotif{
        {"jsonrpc", "2.0"},
        {"method", "textDocument/didChange"},
        {"params", std::move(changeParams)}
    };
    server.handleMessage(llvm::json::Value(std::move(changeNotif)));

    auto fixedDiagVal = parseResponse(out);
    ASSERT_TRUE(fixedDiagVal.has_value());
    auto fixedDiagParams = fixedDiagVal->getAsObject()->getObject("params");
    auto fixedDiags = fixedDiagParams->getArray("diagnostics");
    ASSERT_TRUE(fixedDiags != nullptr);
    ASSERT_TRUE(fixedDiags->empty()); // 0 errors now!

    // 3. didOpen for XML file -> must NOT publish Java errors
    std::string xmlUri = "file:///tmp/SampleMapper.xml";
    std::string xmlSrc =
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<mapper namespace=\"com.sample.SampleMapper\">\n"
        "    <select id=\"selectById\" resultType=\"com.sample.Sample\">\n"
        "        SELECT * FROM sample WHERE id = #{id}\n"
        "    </select>\n"
        "</mapper>\n";

    llvm::json::Object xmlOpenParams{
        {"textDocument", llvm::json::Object{
            {"uri", xmlUri},
            {"languageId", "xml"},
            {"version", 1},
            {"text", xmlSrc}
        }}
    };
    llvm::json::Object xmlOpenNotif{
        {"jsonrpc", "2.0"},
        {"method", "textDocument/didOpen"},
        {"params", std::move(xmlOpenParams)}
    };
    server.handleMessage(llvm::json::Value(std::move(xmlOpenNotif)));

    auto xmlDiagVal = parseResponse(out);
    ASSERT_TRUE(xmlDiagVal.has_value());
    auto xmlDiagParams = xmlDiagVal->getAsObject()->getObject("params");
    auto xmlDiags = xmlDiagParams->getArray("diagnostics");
    ASSERT_TRUE(xmlDiags != nullptr);
    ASSERT_TRUE(xmlDiags->empty()); // Exactly 0 errors on XML!

    // 4. didClose
    llvm::json::Object closeParams{
        {"textDocument", llvm::json::Object{{"uri", uri}}}
    };
    llvm::json::Object closeNotif{
        {"jsonrpc", "2.0"},
        {"method", "textDocument/didClose"},
        {"params", std::move(closeParams)}
    };
    server.handleMessage(llvm::json::Value(std::move(closeNotif)));
}

TEST(LspServerTest, DefinitionAndPrimitiveKeywordExclusion) {
    std::stringstream in;
    std::stringstream out;
    Transport transport(in, out);
    Server server(transport);

    std::string uri = "file:///tmp/DefProbe.java";
    std::string src =
        "package com.sample;\n"
        "public class DefProbe {\n"
        "    private int count;\n"
        "    public int getCount() {\n"
        "        int localVar = this.count;\n"
        "        return localVar;\n"
        "    }\n"
        "}\n";

    llvm::json::Object openParams{
        {"textDocument", llvm::json::Object{
            {"uri", uri},
            {"languageId", "java"},
            {"version", 1},
            {"text", src}
        }}
    };
    server.handleMessage(llvm::json::Value(llvm::json::Object{
        {"jsonrpc", "2.0"}, {"method", "textDocument/didOpen"}, {"params", std::move(openParams)}
    }));
    parseResponse(out); // discard didOpen diagnostics

    // 1. Definition on local variable "localVar" at line 5: "return localVar;"
    llvm::json::Object defReq1{
        {"jsonrpc", "2.0"},
        {"id", 10},
        {"method", "textDocument/definition"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}},
            {"position", llvm::json::Object{{"line", 5}, {"character", 17}}}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(defReq1)));
    auto resp1 = parseResponse(out);
    ASSERT_TRUE(resp1.has_value());
    auto arr1 = resp1->getAsObject()->getArray("result");
    ASSERT_TRUE(arr1 != nullptr);
    ASSERT_TRUE(!arr1->empty());
    // Defined at line 4
    int defLine1 = (*arr1)[0].getAsObject()->getObject("range")->getObject("start")->getInteger("line").value_or(-1);
    ASSERT_EQ(defLine1, 4);

    // 2. Definition on primitive type "int" at line 4 (character 9: "int localVar")
    // Must return empty list, NOT crash and NOT report "int.java 不存在"
    llvm::json::Object defReq2{
        {"jsonrpc", "2.0"},
        {"id", 11},
        {"method", "textDocument/definition"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}},
            {"position", llvm::json::Object{{"line", 4}, {"character", 9}}}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(defReq2)));
    auto resp2 = parseResponse(out);
    ASSERT_TRUE(resp2.has_value());
    auto arr2 = resp2->getAsObject()->getArray("result");
    ASSERT_TRUE(arr2 != nullptr);
    ASSERT_TRUE(arr2->empty());

    // 3. Implementation on keyword "int" or "void" -> must return empty list
    llvm::json::Object implReq{
        {"jsonrpc", "2.0"},
        {"id", 12},
        {"method", "textDocument/implementation"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}},
            {"position", llvm::json::Object{{"line", 4}, {"character", 9}}}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(implReq)));
    auto resp3 = parseResponse(out);
    ASSERT_TRUE(resp3.has_value());
    auto arr3 = resp3->getAsObject()->getArray("result");
    ASSERT_TRUE(arr3 != nullptr);
    ASSERT_TRUE(arr3->empty());

    // 4. Declaration on primitive type "int" -> must return empty list
    llvm::json::Object declReq{
        {"jsonrpc", "2.0"},
        {"id", 13},
        {"method", "textDocument/declaration"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}},
            {"position", llvm::json::Object{{"line", 4}, {"character", 9}}}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(declReq)));
    auto resp4 = parseResponse(out);
    ASSERT_TRUE(resp4.has_value());
    auto arr4 = resp4->getAsObject()->getArray("result");
    ASSERT_TRUE(arr4 != nullptr);
    ASSERT_TRUE(arr4->empty());
}

TEST(LspServerTest, MapperImplementationNoFakeImpl) {
    std::stringstream in;
    std::stringstream out;
    Transport transport(in, out);
    Server server(transport);

    std::string uri = "file:///tmp/UserMapper.java";
    std::string src =
        "package com.sample.mapper;\n"
        "public interface UserMapper {\n"
        "    int countUsers();\n"
        "}\n";

    llvm::json::Object openParams{
        {"textDocument", llvm::json::Object{
            {"uri", uri},
            {"languageId", "java"},
            {"version", 1},
            {"text", src}
        }}
    };
    server.handleMessage(llvm::json::Value(llvm::json::Object{
        {"jsonrpc", "2.0"}, {"method", "textDocument/didOpen"}, {"params", std::move(openParams)}
    }));
    parseResponse(out);

    // Calling textDocument/implementation on UserMapper without XML -> must NOT jump to fake UserMapperImpl.java
    llvm::json::Object implReq{
        {"jsonrpc", "2.0"},
        {"id", 20},
        {"method", "textDocument/implementation"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}},
            {"position", llvm::json::Object{{"line", 2}, {"character", 10}}} // on countUsers
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(implReq)));
    auto resp = parseResponse(out);
    ASSERT_TRUE(resp.has_value());
    auto arr = resp->getAsObject()->getArray("result");
    ASSERT_TRUE(arr != nullptr);
    // Must be empty or jump to real XML statement, but NEVER to UserMapperImpl.java
    for (const auto& item : *arr) {
        std::string locUri = item.getAsObject()->getString("uri").value_or("").str();
        ASSERT_TRUE(locUri.find("UserMapperImpl.java") == std::string::npos);
    }
}

TEST(LspServerTest, PrepareRenameAndScopedRename) {
    std::stringstream in;
    std::stringstream out;
    Transport transport(in, out);
    Server server(transport);

    std::string uri = "file:///tmp/RenameProbe.java";
    std::string src =
        "package com.sample;\n"
        "public class RenameProbe {\n"
        "    private String name;\n"
        "    public void methodA() {\n"
        "        String user = \"A\";\n"
        "        System.out.println(user);\n"
        "    }\n"
        "    public void methodB() {\n"
        "        String user = \"B\";\n"
        "        System.out.println(user);\n"
        "    }\n"
        "}\n";

    llvm::json::Object openParams{
        {"textDocument", llvm::json::Object{
            {"uri", uri},
            {"languageId", "java"},
            {"version", 1},
            {"text", src}
        }}
    };
    server.handleMessage(llvm::json::Value(llvm::json::Object{
        {"jsonrpc", "2.0"}, {"method", "textDocument/didOpen"}, {"params", std::move(openParams)}
    }));
    parseResponse(out);

    // 1. prepareRename on keyword "void" -> should return null
    llvm::json::Object prepKeyword{
        {"jsonrpc", "2.0"},
        {"id", 30},
        {"method", "textDocument/prepareRename"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}},
            {"position", llvm::json::Object{{"line", 3}, {"character", 12}}} // on "void"
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(prepKeyword)));
    auto prepResp1 = parseResponse(out);
    ASSERT_TRUE(prepResp1.has_value());
    ASSERT_TRUE(prepResp1->getAsObject()->get("result")->getAsNull().has_value());

    // 2. prepareRename on local variable "user" at line 4 (character 16) -> should succeed
    llvm::json::Object prepVar{
        {"jsonrpc", "2.0"},
        {"id", 31},
        {"method", "textDocument/prepareRename"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}},
            {"position", llvm::json::Object{{"line", 4}, {"character", 16}}} // on "user"
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(prepVar)));
    auto prepResp2 = parseResponse(out);
    ASSERT_TRUE(prepResp2.has_value());
    auto prepObj2 = prepResp2->getAsObject()->getObject("result");
    ASSERT_TRUE(prepObj2 != nullptr);
    ASSERT_EQ(std::string(prepObj2->getString("placeholder").value_or("")), "user");

    // 3. Rename local variable "user" in methodA to "newUser"
    llvm::json::Object renameReq{
        {"jsonrpc", "2.0"},
        {"id", 32},
        {"method", "textDocument/rename"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}},
            {"position", llvm::json::Object{{"line", 4}, {"character", 16}}},
            {"newName", "newUser"}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(renameReq)));
    auto renameResp = parseResponse(out);
    ASSERT_TRUE(renameResp.has_value());
    auto weObj = renameResp->getAsObject()->getObject("result");
    ASSERT_TRUE(weObj != nullptr);
    auto changes = weObj->getObject("changes");
    ASSERT_TRUE(changes != nullptr);
    auto edits = changes->getArray(uri);
    ASSERT_TRUE(edits != nullptr);

    // CRITICAL: methodA's local variable "user" must ONLY rename lines 4 and 5 in methodA!
    // It must NEVER touch methodB's "user" at lines 8 and 9!
    ASSERT_EQ(edits->size(), 2);
    for (const auto& e : *edits) {
        int l = e.getAsObject()->getObject("range")->getObject("start")->getInteger("line").value_or(-1);
        ASSERT_TRUE(l == 4 || l == 5);
        ASSERT_TRUE(l != 8 && l != 9);
    }
}

TEST(LspServerTest, FormattingAndCodeAction) {
    std::stringstream in;
    std::stringstream out;
    Transport transport(in, out);
    Server server(transport);

    std::string uri = "file:///tmp/FormatProbe.java";
    std::string unformatted =
        "package com.sample;\n"
        "public class FormatProbe {\n"
        "public void run(){\n"
        "int x = 1;\n"
        "}\n"
        "}\n";

    llvm::json::Object openParams{
        {"textDocument", llvm::json::Object{
            {"uri", uri},
            {"languageId", "java"},
            {"version", 1},
            {"text", unformatted}
        }}
    };
    server.handleMessage(llvm::json::Value(llvm::json::Object{
        {"jsonrpc", "2.0"}, {"method", "textDocument/didOpen"}, {"params", std::move(openParams)}
    }));
    parseResponse(out);

    // 1. Formatting
    llvm::json::Object fmtReq{
        {"jsonrpc", "2.0"},
        {"id", 40},
        {"method", "textDocument/formatting"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(fmtReq)));
    auto fmtResp = parseResponse(out);
    ASSERT_TRUE(fmtResp.has_value());
    auto fmtEdits = fmtResp->getAsObject()->getArray("result");
    ASSERT_TRUE(fmtEdits != nullptr);
    ASSERT_TRUE(!fmtEdits->empty());
    std::string newText = (*fmtEdits)[0].getAsObject()->getString("newText").value_or("").str();
    // Lines inside method must be indented with 8 spaces
    ASSERT_TRUE(newText.find("        int x = 1;") != std::string::npos);

    // 2. Code Action
    llvm::json::Object caReq{
        {"jsonrpc", "2.0"},
        {"id", 41},
        {"method", "textDocument/codeAction"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}},
            {"range", llvm::json::Object{
                {"start", llvm::json::Object{{"line", 0}, {"character", 0}}},
                {"end", llvm::json::Object{{"line", 1}, {"character", 0}}}
            }},
            {"context", llvm::json::Object{
                {"diagnostics", llvm::json::Array{}}
            }}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(caReq)));
    auto caResp = parseResponse(out);
    ASSERT_TRUE(caResp.has_value());
    auto actions = caResp->getAsObject()->getArray("result");
    ASSERT_TRUE(actions != nullptr);
    ASSERT_TRUE(!actions->empty());
    ASSERT_EQ(std::string((*actions)[0].getAsObject()->getString("title").value_or("")), "Organize Imports");
}

TEST(LspServerTest, DocumentSymbolsAndSignatureHelp) {
    std::stringstream in;
    std::stringstream out;
    Transport transport(in, out);
    Server server(transport);

    std::string uri = "file:///tmp/SymbolProbe.java";
    std::string src =
        "package com.sample;\n"
        "public class SymbolProbe {\n"
        "    private int value;\n"
        "    public void calculate(int a, int b) {\n"
        "        String.format(\"%d %d\", a, b);\n"
        "    }\n"
        "}\n";

    llvm::json::Object openParams{
        {"textDocument", llvm::json::Object{
            {"uri", uri},
            {"languageId", "java"},
            {"version", 1},
            {"text", src}
        }}
    };
    server.handleMessage(llvm::json::Value(llvm::json::Object{
        {"jsonrpc", "2.0"}, {"method", "textDocument/didOpen"}, {"params", std::move(openParams)}
    }));
    parseResponse(out);

    // 1. DocumentSymbol
    llvm::json::Object docSymReq{
        {"jsonrpc", "2.0"},
        {"id", 50},
        {"method", "textDocument/documentSymbol"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}}
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(docSymReq)));
    auto symResp = parseResponse(out);
    ASSERT_TRUE(symResp.has_value());
    auto symbols = symResp->getAsObject()->getArray("result");
    ASSERT_TRUE(symbols != nullptr);
    ASSERT_TRUE(!symbols->empty());
    // Outermost class "SymbolProbe"
    ASSERT_EQ(std::string((*symbols)[0].getAsObject()->getString("name").value_or("")), "SymbolProbe");

    // 2. SignatureHelp at "String.format(\"%d %d\", a, b);"
    llvm::json::Object sigReq{
        {"jsonrpc", "2.0"},
        {"id", 51},
        {"method", "textDocument/signatureHelp"},
        {"params", llvm::json::Object{
            {"textDocument", llvm::json::Object{{"uri", uri}}},
            {"position", llvm::json::Object{{"line", 4}, {"character", 31}}} // after first comma
        }}
    };
    server.handleMessage(llvm::json::Value(std::move(sigReq)));
    auto sigResp = parseResponse(out);
    ASSERT_TRUE(sigResp.has_value());
    auto sigObj = sigResp->getAsObject()->getObject("result");
    ASSERT_TRUE(sigObj != nullptr);
    auto sigs = sigObj->getArray("signatures");
    ASSERT_TRUE(sigs != nullptr);
    ASSERT_TRUE(!sigs->empty());
    ASSERT_TRUE((*sigs)[0].getAsObject()->getString("label").value_or("").find("format") != std::string::npos);
}
