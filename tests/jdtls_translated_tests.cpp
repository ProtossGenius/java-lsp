#include "test_framework.h"
#include "lsp/transport.h"
#include "lsp/server.h"
#include "semantics/symbol_table.h"
#include "semantics/lombok.h"
#include "semantics/type_checker.h"
#include "project/decompilation.h"
#include <tree_sitter/api.h>
#include <sstream>

using namespace javalsp;

// Translated from Eclipse JDT LS test suite (CompletionTest, NavigationTest, DiagnosticsTest)

TEST(JdtlsTranslatedTest, CompletionMemberAndRanking) {
    auto& symTab = SymbolTable::instance();
    CompletionEngine engine(symTab);

    // Register User & UserService & CreateUserRequest
    JavaClass reqCls;
    reqCls.name = "CreateUserRequest";
    reqCls.packageName = "com.example.demo.model";
    reqCls.fqn = "com.example.demo.model.CreateUserRequest";
    reqCls.annotations.push_back("Data");
    JavaField f1{"name", JavaTypeRef{"String", "java.lang.String"}};
    JavaField f2{"email", JavaTypeRef{"String", "java.lang.String"}};
    reqCls.fields.push_back(f1);
    reqCls.fields.push_back(f2);
    LombokHandler::processClass(reqCls);
    symTab.addClass(reqCls);

    JavaClass svcCls;
    svcCls.name = "UserService";
    svcCls.packageName = "com.example.demo.service";
    svcCls.fqn = "com.example.demo.service.UserService";
    svcCls.kind = ClassKind::INTERFACE;
    JavaMethod mList;
    mList.name = "listUsers";
    mList.returnType = JavaTypeRef{"List", "java.util.List", {JavaTypeRef{"User", "com.example.demo.model.User"}}};
    mList.docComment = "Lists all persisted users.";
    svcCls.methods.push_back(mList);

    JavaMethod mGet;
    mGet.name = "getUser";
    mGet.returnType = JavaTypeRef{"User", "com.example.demo.model.User"};
    mGet.parameters.push_back({JavaTypeRef{"Long", "java.lang.Long"}, "id"});
    svcCls.methods.push_back(mGet);
    symTab.addClass(svcCls);

    JavaCompilationUnit cu;
    cu.packageName = "com.example.demo.controller";
    cu.imports.push_back("com.example.demo.model.CreateUserRequest");
    cu.imports.push_back("com.example.demo.service.UserService");
    cu.imports.push_back("java.util.List");

    auto fileScope = Scope::create(ScopeKind::FILE);
    auto classScope = Scope::create(ScopeKind::CLASS, fileScope, Range{{0, 0}, {100, 0}});
    auto methodScope = Scope::create(ScopeKind::METHOD, classScope, Range{{0, 0}, {100, 0}});

    JavaVariable reqVar{"request", JavaTypeRef{"CreateUserRequest", "com.example.demo.model.CreateUserRequest"}};
    methodScope->addVariable(reqVar);

    JavaVariable svcVar{"userService", JavaTypeRef{"UserService", "com.example.demo.service.UserService"}};
    methodScope->addVariable(svcVar);

    // 1. Test request. completion
    std::string src1 = "    request.";
    Position pos1{0, 12};
    auto comp1 = engine.complete(cu, src1, pos1, fileScope);

    std::map<std::string, CompletionItem> byLabel;
    for (const auto& item : comp1.items) {
        byLabel[item.label] = item;
    }
    ASSERT_TRUE(byLabel.find("getName") != byLabel.end());
    ASSERT_TRUE(byLabel.find("getEmail") != byLabel.end());
    ASSERT_TRUE(byLabel.find("setName") != byLabel.end());
    ASSERT_TRUE(byLabel.find("setEmail") != byLabel.end());
    ASSERT_EQ(byLabel["setEmail"].insertText, "setEmail(${1:email})");

    // 2. Test List<User> users = userService. ranking
    std::string src2 = "    List<User> users = userService.";
    Position pos2{0, 35};
    auto comp2 = engine.complete(cu, src2, pos2, fileScope);
    ASSERT_TRUE(!comp2.items.empty());
    // listUsers should rank first!
    ASSERT_EQ(comp2.items[0].label, "listUsers");

    // 3. Test List. static completion
    std::string src3 = "    List.";
    Position pos3{0, 9};
    auto comp3 = engine.complete(cu, src3, pos3, fileScope);
    bool hasOf = false;
    for (const auto& it : comp3.items) {
        if (it.label == "of") hasOf = true;
    }
    ASSERT_TRUE(hasOf);
}

TEST(JdtlsTranslatedTest, HoverAndDocumentation) {
    auto& symTab = SymbolTable::instance();
    const JavaClass* svc = symTab.findClassByName("UserService");
    ASSERT_TRUE(svc != nullptr);

    bool foundListUsers = false;
    for (const auto& m : svc->methods) {
        if (m.name == "listUsers") {
            foundListUsers = true;
            ASSERT_EQ(m.docComment, "Lists all persisted users.");
            ASSERT_EQ(m.returnType.name, "List");
        }
    }
    ASSERT_TRUE(foundListUsers);
}

TEST(JdtlsTranslatedTest, UnresolvedDiagnostics) {
    auto& symTab = SymbolTable::instance();

    JavaCompilationUnit cu;
    cu.packageName = "com.example.demo";

    std::string src =
        "package com.example.demo;\n"
        "public class DiagnosticsProbe {\n"
        "  void run() {\n"
        "    User1 missing = null;\n"
        "  }\n"
        "}\n";

    JavaParser parser;
    ASSERT_TRUE(parser.init());
    auto parsedCu = parser.parse(src, "DiagnosticsProbe.java");
    ASSERT_TRUE(parsedCu != nullptr);

    auto diags = TypeChecker::check(*parsedCu, src, TSNode{}, symTab);
    ASSERT_TRUE(!diags.empty());

    bool foundUser1 = false;
    for (const auto& d : diags) {
        if (d.message.find("User1") != std::string::npos) {
            foundUser1 = true;
            ASSERT_EQ(d.severity, 1); // Error
        }
    }
    ASSERT_TRUE(foundUser1);
}

TEST(JdtlsTranslatedTest, DecompilationAndNavigationSources) {
    auto& decompile = DecompilationService::instance();

    // JDK standard library navigation (List.java)
    auto listSrc = decompile.resolveSourceForFqn("java.util.List");
    ASSERT_TRUE(listSrc.has_value());
    ASSERT_TRUE(listSrc->find("List.java") != std::string::npos);

    // SLF4J Logger navigation (Logger.java)
    auto loggerSrc = decompile.resolveSourceForFqn("org.slf4j.Logger");
    ASSERT_TRUE(loggerSrc.has_value());
    ASSERT_TRUE(loggerSrc->find("Logger.java") != std::string::npos);

    // Logback classic Logger implementation navigation
    auto logbackSrc = decompile.resolveSourceForFqn("ch.qos.logback.classic.Logger");
    ASSERT_TRUE(logbackSrc.has_value());
    ASSERT_TRUE(logbackSrc->find("logback-classic") != std::string::npos);
}

TEST(JdtlsTranslatedTest, InnerClassResolutionAndNoFalsePositive) {
    auto& symTab = SymbolTable::instance();
    JavaParser parser;
    ASSERT_TRUE(parser.init());

    std::string src = R"java(
package org.ref;

public class Apple {
    public static <T> AppleBuilder<T> builder() {
        return new AppleBuilder<T>();
    }

    public static class AppleBuilder<T> {
        private String name;
        public AppleBuilder<T> name(String name) {
            this.name = name;
            return this;
        }
        public Apple build() {
            return new Apple();
        }
    }
}
)java";

    auto cu = parser.parse(src, "Apple.java");
    ASSERT_TRUE(cu != nullptr);

    // Check inner class resolution
    const JavaClass* resolved = symTab.resolveClass("AppleBuilder", *cu);
    ASSERT_TRUE(resolved != nullptr);

    auto diags = TypeChecker::check(*cu, src, TSNode{}, symTab);
    ASSERT_TRUE(diags.empty());
}

TEST(JdtlsTranslatedTest, ScopedTypeIdentifierPackagePrefixResolution) {
    auto& symTab = SymbolTable::instance();
    JavaParser parser;
    ASSERT_TRUE(parser.init());

    std::string src = R"java(
package org.sample;

public class ScopedTypeProbe {
    public void run() {
        java.util.StringTokenizer tokenizer = null;
    }
}
)java";

    auto cu = parser.parse(src, "ScopedTypeProbe.java");
    ASSERT_TRUE(cu != nullptr);

    auto diags = TypeChecker::check(*cu, src, TSNode{}, symTab);
    for (const auto& d : diags) {
        std::cerr << "Diagnostic: " << d.message << " at line " << d.range.start.line << std::endl;
    }
    ASSERT_TRUE(diags.empty());
}

TEST(JdtlsTranslatedTest, UnimportedAnnotationReportsDiagnostic) {
    auto& symTab = SymbolTable::instance();
    JavaParser parser;
    ASSERT_TRUE(parser.init());

    std::string src = R"java(
package com.example.demo.model;

@Table("user")
public class User {
    private Long id;
}
)java";

    auto cu = parser.parse(src, "User.java");
    ASSERT_TRUE(cu != nullptr);

    auto diags = TypeChecker::check(*cu, src, TSNode{}, symTab);
    ASSERT_TRUE(!diags.empty());
    bool foundTableError = false;
    for (const auto& d : diags) {
        if (d.message.find("Table cannot be resolved to a type") != std::string::npos) {
            foundTableError = true;
            break;
        }
    }
    ASSERT_TRUE(foundTableError);
}

TEST(JdtlsTranslatedTest, ImportedClassInnerClassResolutionWithDollarFqn) {
    auto& symTab = SymbolTable::instance();
    // Simulate JarIndexer registering an inner class with dollar sign
    JavaClass innerCls;
    innerCls.name = "Parameters";
    innerCls.packageName = "org.junit.runners";
    innerCls.fqn = "org.junit.runners.Parameterized$Parameters";
    innerCls.kind = ClassKind::CLASS;
    symTab.addClass(innerCls);

    // Verify findClassByFqn works with both dollar and dot
    const JavaClass* byDollar = symTab.findClassByFqn("org.junit.runners.Parameterized$Parameters");
    ASSERT_TRUE(byDollar != nullptr);
    const JavaClass* byDot = symTab.findClassByFqn("org.junit.runners.Parameterized.Parameters");
    ASSERT_TRUE(byDot != nullptr);
    ASSERT_EQ(byDollar, byDot);

    // Also register an inner class with dot and verify lookup with dollar
    JavaClass dotInnerCls;
    dotInnerCls.name = "Builder";
    dotInnerCls.packageName = "com.sample";
    dotInnerCls.fqn = "com.sample.Container.Builder";
    dotInnerCls.kind = ClassKind::CLASS;
    symTab.addClass(dotInnerCls);

    const JavaClass* dotFoundByDollar = symTab.findClassByFqn("com.sample.Container$Builder");
    ASSERT_TRUE(dotFoundByDollar != nullptr);
    const JavaClass* dotFoundByDot = symTab.findClassByFqn("com.sample.Container.Builder");
    ASSERT_TRUE(dotFoundByDot != nullptr);
    ASSERT_EQ(dotFoundByDollar, dotFoundByDot);

    JavaParser parser;
    ASSERT_TRUE(parser.init());

    std::string src = R"java(
package com.sample;

import org.junit.runners.Parameterized;

public class TestSample {
    @Parameterized.Parameters
    public void run() {}
}
)java";

    auto cu = parser.parse(src, "TestSample.java");
    ASSERT_TRUE(cu != nullptr);

    // Verify resolveClass works with both dot and dollar
    const JavaClass* resolvedByDot = symTab.resolveClass("Parameterized.Parameters", *cu);
    ASSERT_TRUE(resolvedByDot != nullptr);
    const JavaClass* resolvedByDollar = symTab.resolveClass("Parameterized$Parameters", *cu);
    ASSERT_TRUE(resolvedByDollar != nullptr);
    ASSERT_EQ(resolvedByDot, resolvedByDollar);

    auto diags = TypeChecker::check(*cu, src, TSNode{}, symTab);
    bool hasDiag = false;
    for (const auto& d : diags) {
        if (d.message.find("Parameterized.Parameters") != std::string::npos) {
            hasDiag = true;
            break;
        }
    }
    ASSERT_FALSE(hasDiag);
}
