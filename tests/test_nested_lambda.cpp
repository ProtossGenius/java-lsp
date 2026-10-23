#include "test_framework.h"
#include "semantics/scope.h"
#include "semantics/nested_lambda.h"
#include "parser/tree_sitter_loader.h"

using namespace javalsp;

TEST(NestedLambdaTest, ScopeHierarchyAndParamResolution) {
    auto fileScope = Scope::create(ScopeKind::FILE);
    auto classScope = Scope::create(ScopeKind::CLASS, fileScope, Range{{0, 0}, {100, 0}});

    JavaVariable field;
    field.name = "userService";
    field.type = JavaTypeRef{"UserService", "com.example.demo.service.UserService"};
    classScope->addVariable(field);

    auto methodScope = Scope::create(ScopeKind::METHOD, classScope, Range{{10, 0}, {90, 0}});

    // Local variable: List<Integer> list
    JavaVariable listVar;
    listVar.name = "list";
    listVar.type = JavaTypeRef{"List", "java.util.List", {JavaTypeRef{"Integer", "java.lang.Integer"}}};
    methodScope->addVariable(listVar);

    // Local variable: List<User> items
    JavaVariable itemsVar;
    itemsVar.name = "items";
    itemsVar.type = JavaTypeRef{"List", "java.util.List", {JavaTypeRef{"User", "com.example.demo.model.User"}}};
    methodScope->addVariable(itemsVar);

    // Outer lambda: x -> ...
    auto outerLambda = std::make_shared<JavaLambda>();
    outerLambda->range = Range{{20, 0}, {50, 0}};
    auto outerScope = Scope::create(ScopeKind::LAMBDA, methodScope, outerLambda->range);

    JavaVariable xVar;
    xVar.name = "x";
    xVar.type = JavaTypeRef{"Integer", "java.lang.Integer"};
    xVar.nameRange = Range{{20, 10}, {20, 11}};
    outerScope->addVariable(xVar);

    // Inner lambda: y -> ...
    auto innerLambda = std::make_shared<JavaLambda>();
    innerLambda->range = Range{{25, 0}, {40, 0}};
    innerLambda->parentLambda = outerLambda;
    auto innerScope = Scope::create(ScopeKind::LAMBDA, outerScope, innerLambda->range);

    JavaVariable yVar;
    yVar.name = "y";
    yVar.type = JavaTypeRef{"User", "com.example.demo.model.User"};
    yVar.nameRange = Range{{25, 10}, {25, 11}};
    innerScope->addVariable(yVar);

    // Test inside inner scope (line 30)
    Position innerPos{30, 5};
    auto activeScope = fileScope->findInnermostScope(innerPos);
    ASSERT_TRUE(activeScope != nullptr);

    // Lookup 'y' inside inner lambda
    auto resolvedY = activeScope->lookupVariable("y");
    ASSERT_TRUE(resolvedY.has_value());
    ASSERT_EQ(resolvedY->name, "y");
    ASSERT_EQ(resolvedY->type.name, "User");

    // Lookup 'x' inside inner lambda (resolves to outer lambda!)
    auto resolvedX = activeScope->lookupVariable("x");
    ASSERT_TRUE(resolvedX.has_value());
    ASSERT_EQ(resolvedX->name, "x");
    ASSERT_EQ(resolvedX->type.name, "Integer");

    // Lookup 'list' inside inner lambda (resolves to method scope!)
    auto resolvedList = activeScope->lookupVariable("list");
    ASSERT_TRUE(resolvedList.has_value());
    ASSERT_EQ(resolvedList->name, "list");

    // Lookup 'userService' inside inner lambda (resolves to class field!)
    auto resolvedService = activeScope->lookupVariable("userService");
    ASSERT_TRUE(resolvedService.has_value());
    ASSERT_EQ(resolvedService->name, "userService");

    // Test outside inner scope (line 45, inside outer lambda)
    Position outerPos{45, 5};
    auto activeOuterScope = fileScope->findInnermostScope(outerPos);
    ASSERT_TRUE(activeOuterScope != nullptr);
    ASSERT_TRUE(activeOuterScope->lookupVariable("x").has_value());
    ASSERT_FALSE(activeOuterScope->lookupVariable("y").has_value()); // 'y' is NOT visible outside inner lambda!
}
