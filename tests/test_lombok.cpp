#include "test_framework.h"
#include "parser/java_parser.h"
#include "semantics/lombok.h"

using namespace javalsp;

TEST(LombokTest, Slf4jAnnotation) {
    JavaClass cls;
    cls.name = "UserController";
    cls.annotations.push_back("Slf4j");

    LombokHandler::processClass(cls);

    bool hasLog = false;
    for (const auto& f : cls.fields) {
        if (f.name == "log") {
            hasLog = true;
            ASSERT_EQ(f.type.name, "Logger");
            ASSERT_EQ(f.type.fqn, "org.slf4j.Logger");
            ASSERT_TRUE(f.isStatic());
            ASSERT_TRUE(f.isFinal());
        }
    }
    ASSERT_TRUE(hasLog);
}

TEST(LombokTest, DataAnnotationGettersAndSetters) {
    JavaClass cls;
    cls.name = "CreateUserRequest";
    cls.annotations.push_back("Data");

    JavaField f1;
    f1.name = "name";
    f1.type = JavaTypeRef{"String", "java.lang.String"};
    cls.fields.push_back(f1);

    JavaField f2;
    f2.name = "email";
    f2.type = JavaTypeRef{"String", "java.lang.String"};
    cls.fields.push_back(f2);

    LombokHandler::processClass(cls);

    bool hasGetName = false;
    bool hasSetName = false;
    bool hasGetEmail = false;
    bool hasSetEmail = false;

    for (const auto& m : cls.methods) {
        if (m.name == "getName") hasGetName = true;
        if (m.name == "setName") {
            hasSetName = true;
            ASSERT_EQ(m.parameters.size(), 1);
            ASSERT_EQ(m.parameters[0].first.name, "String");
        }
        if (m.name == "getEmail") hasGetEmail = true;
        if (m.name == "setEmail") {
            hasSetEmail = true;
            ASSERT_EQ(m.parameters.size(), 1);
            ASSERT_EQ(m.parameters[0].first.name, "String");
        }
    }

    ASSERT_TRUE(hasGetName);
    ASSERT_TRUE(hasSetName);
    ASSERT_TRUE(hasGetEmail);
    ASSERT_TRUE(hasSetEmail);
}

TEST(LombokTest, RequiredArgsConstructor) {
    JavaClass cls;
    cls.name = "UserController";
    cls.annotations.push_back("RequiredArgsConstructor");

    JavaField f1;
    f1.name = "userService";
    f1.type = JavaTypeRef{"UserService", "com.example.demo.service.UserService"};
    f1.modifiers = {"private", "final"};
    cls.fields.push_back(f1);

    LombokHandler::processClass(cls);

    bool hasCtor = false;
    for (const auto& m : cls.methods) {
        if (m.isConstructor && m.parameters.size() == 1) {
            hasCtor = true;
            ASSERT_EQ(m.parameters[0].second, "userService");
            ASSERT_EQ(m.parameters[0].first.name, "UserService");
        }
    }
    ASSERT_TRUE(hasCtor);
}
