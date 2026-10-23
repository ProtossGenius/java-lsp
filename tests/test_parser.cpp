#include "test_framework.h"
#include "parser/java_parser.h"

using namespace javalsp;

TEST(ParserTest, BasicClassAndMethods) {
    JavaParser parser;
    ASSERT_TRUE(parser.init());

    std::string src =
        "package com.example.demo;\n"
        "import java.util.List;\n"
        "public class Calculator {\n"
        "  private int count = 0;\n"
        "  /** Calculates sum */\n"
        "  public int add(int a, int b) {\n"
        "    return a + b;\n"
        "  }\n"
        "}\n";

    auto cu = parser.parse(src, "Calculator.java");
    ASSERT_TRUE(cu != nullptr);
    ASSERT_EQ(cu->packageName, "com.example.demo");
    ASSERT_EQ(cu->imports.size(), 1);
    ASSERT_EQ(cu->imports[0], "java.util.List");
    ASSERT_EQ(cu->classes.size(), 1);

    const auto& cls = cu->classes[0];
    ASSERT_EQ(cls.name, "Calculator");
    ASSERT_EQ(cls.fqn, "com.example.demo.Calculator");
    ASSERT_EQ(cls.fields.size(), 1);
    ASSERT_EQ(cls.fields[0].name, "count");
    ASSERT_EQ(cls.fields[0].type.name, "int");

    ASSERT_EQ(cls.methods.size(), 1);
    ASSERT_EQ(cls.methods[0].name, "add");
    ASSERT_EQ(cls.methods[0].returnType.name, "int");
    ASSERT_EQ(cls.methods[0].parameters.size(), 2);
    ASSERT_EQ(cls.methods[0].parameters[0].second, "a");
    ASSERT_EQ(cls.methods[0].parameters[1].second, "b");
}

TEST(ParserTest, GenericTypesAndAnnotations) {
    JavaParser parser;
    ASSERT_TRUE(parser.init());

    std::string src =
        "package com.example.demo.model;\n"
        "import lombok.Data;\n"
        "@Data\n"
        "public class CreateUserRequest {\n"
        "  private String name;\n"
        "  private String email;\n"
        "}\n";

    auto cu = parser.parse(src, "CreateUserRequest.java");
    ASSERT_TRUE(cu != nullptr);
    ASSERT_EQ(cu->classes.size(), 1);
    const auto& cls = cu->classes[0];
    ASSERT_TRUE(cls.hasAnnotation("Data"));
    ASSERT_EQ(cls.fields.size(), 2);
    ASSERT_EQ(cls.fields[0].name, "name");
    ASSERT_EQ(cls.fields[1].name, "email");
}
