#include "test_framework.h"
#include "completion/graph_search.h"
#include "completion/completion_engine.h"
#include "semantics/symbol_table.h"

using namespace javalsp;

TEST(ChainedCompletionTest, GraphSearchPaths) {
    TypeGraphSearch graphSearch;

    JavaTypeRef srcType{"List", "java.util.List", {JavaTypeRef{"Integer", "java.lang.Integer"}}};
    JavaTypeRef targetType{"List", "java.util.List", {JavaTypeRef{"String", "java.lang.String"}}};

    auto paths = graphSearch.searchPaths(srcType, targetType);
    ASSERT_TRUE(!paths.empty());

    bool foundStreamMapToList = false;
    for (const auto& p : paths) {
        if (p.insertText == "stream().map($1-it->$2).toList()") {
            foundStreamMapToList = true;
            ASSERT_EQ(p.chainLength, 3);
        }
    }
    ASSERT_TRUE(foundStreamMapToList);

    // Verify ordering: chainLength ascending, then alphabetical
    for (size_t i = 1; i < paths.size(); ++i) {
        if (paths[i - 1].chainLength == paths[i].chainLength) {
            ASSERT_TRUE(paths[i - 1].label <= paths[i].label);
        } else {
            ASSERT_TRUE(paths[i - 1].chainLength < paths[i].chainLength);
        }
    }
}

TEST(ChainedCompletionTest, FilteringAndSorting) {
    auto& symTab = SymbolTable::instance();
    CompletionEngine engine(symTab);

    JavaCompilationUnit cu;
    cu.packageName = "com.example.demo";

    std::string src =
        "package com.example.demo;\n"
        "import java.util.List;\n"
        "class Test {\n"
        "  void run() {\n"
        "    List<Integer> list = List.of(1);\n"
        "    List<String> strList = list.\n"
        "  }\n"
        "}\n";

    auto fileScope = std::make_shared<Scope>(ScopeKind::FILE);
    auto classScope = std::make_shared<Scope>(ScopeKind::CLASS, fileScope, Range{{0, 0}, {10, 0}});
    auto methodScope = std::make_shared<Scope>(ScopeKind::METHOD, classScope, Range{{3, 0}, {7, 0}});
    classScope->addChild(methodScope);
    fileScope->addChild(classScope);

    JavaVariable listVar;
    listVar.name = "list";
    listVar.type = JavaTypeRef{"List", "java.util.List", {JavaTypeRef{"Integer", "java.lang.Integer"}}};
    methodScope->addVariable(listVar);

    // Position at "    List<String> strList = list." (line 5, character 32)
    Position pos{5, 32};
    auto completions = engine.complete(cu, src, pos, fileScope);

    ASSERT_TRUE(!completions.items.empty());

    // 1. Verify that add(Integer) was FILTERED OUT (Requirement 1)
    bool hasAdd = false;
    for (const auto& item : completions.items) {
        if (item.label == "add") {
            hasAdd = true;
            break;
        }
    }
    ASSERT_FALSE(hasAdd);

    // 2. Verify that clear() was FILTERED OUT
    bool hasClear = false;
    for (const auto& item : completions.items) {
        if (item.label == "clear") {
            hasClear = true;
            break;
        }
    }
    ASSERT_FALSE(hasClear);

    // 3. Verify that stream().map($1-it->$2).toList() is PRESENT (Requirement 2)
    bool hasStreamMapToList = false;
    for (const auto& item : completions.items) {
        if (item.insertText == "stream().map($1-it->$2).toList()") {
            hasStreamMapToList = true;
            ASSERT_EQ(item.insertTextFormat, 2); // Snippet format!
            break;
        }
    }
    ASSERT_TRUE(hasStreamMapToList);

    // 4. Verify ranking: shortest chain first, then alphabetical (Requirement 3)
    for (size_t i = 1; i < completions.items.size(); ++i) {
        ASSERT_TRUE(completions.items[i - 1].sortText <= completions.items[i].sortText);
    }
}
