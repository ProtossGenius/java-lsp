#pragma once

#include "lsp/protocol.h"
#include <string>
#include <vector>
#include <memory>
#include <optional>

namespace javalsp {

struct JavaTypeRef {
    std::string name;
    std::string fqn;
    std::vector<JavaTypeRef> typeArguments;
    bool isArray{false};
    bool isPrimitive{false};

    std::string toString() const {
        std::string s = name.empty() ? fqn : name;
        if (!typeArguments.empty()) {
            s += "<";
            for (size_t i = 0; i < typeArguments.size(); ++i) {
                if (i > 0) s += ", ";
                s += typeArguments[i].toString();
            }
            s += ">";
        }
        if (isArray) s += "[]";
        return s;
    }

    bool equals(const JavaTypeRef& o) const {
        if (name != o.name) return false;
        if (typeArguments.size() != o.typeArguments.size()) return false;
        for (size_t i = 0; i < typeArguments.size(); ++i) {
            if (!typeArguments[i].equals(o.typeArguments[i])) return false;
        }
        return isArray == o.isArray;
    }
};

struct JavaField {
    std::string name;
    JavaTypeRef type;
    std::vector<std::string> modifiers;
    std::vector<std::string> annotations;
    Range range;
    Range nameRange;
    std::string docComment;

    bool isFinal() const {
        for (const auto& m : modifiers) if (m == "final") return true;
        return false;
    }
    bool isStatic() const {
        for (const auto& m : modifiers) if (m == "static") return true;
        return false;
    }
};

struct JavaMethod {
    std::string name;
    JavaTypeRef returnType;
    std::vector<std::pair<JavaTypeRef, std::string>> parameters;
    std::vector<std::string> modifiers;
    std::vector<std::string> annotations;
    Range range;
    Range nameRange;
    Range bodyRange;
    std::string docComment;
    bool isConstructor{false};

    std::string signature() const {
        std::string sig = name + "(";
        for (size_t i = 0; i < parameters.size(); ++i) {
            if (i > 0) sig += ", ";
            sig += parameters[i].first.toString() + " " + parameters[i].second;
        }
        sig += ")";
        return sig;
    }
};

struct JavaVariable {
    std::string name;
    JavaTypeRef type;
    Range range;
    Range nameRange;
    bool isFinal{false};
};

struct JavaLambda {
    std::vector<std::pair<JavaTypeRef, std::string>> parameters;
    std::vector<Range> parameterRanges;
    Range range;
    Range bodyRange;
    JavaTypeRef returnType;
    std::vector<JavaVariable> localVariables;
    std::vector<std::shared_ptr<JavaLambda>> nestedLambdas;
    std::weak_ptr<JavaLambda> parentLambda;
};

enum class ClassKind {
    CLASS,
    INTERFACE,
    ENUM,
    RECORD
};

struct JavaClass {
    std::string name;
    std::string packageName;
    std::string fqn;
    std::string filePath;
    ClassKind kind{ClassKind::CLASS};
    std::optional<JavaTypeRef> extendsType;
    std::vector<JavaTypeRef> implementsTypes;
    std::vector<std::string> annotations;
    std::vector<std::string> modifiers;
    std::vector<JavaField> fields;
    std::vector<JavaMethod> methods;
    std::vector<JavaClass> innerClasses;
    Range range;
    Range nameRange;
    std::string docComment;

    bool hasAnnotation(const std::string& annot) const {
        for (const auto& a : annotations) {
            if (a == annot || a == ("@" + annot)) return true;
        }
        return false;
    }
};

struct JavaCompilationUnit {
    std::string filePath;
    std::string uri;
    std::string packageName;
    std::vector<std::string> imports;
    std::vector<JavaClass> classes;
    std::vector<JavaVariable> topLevelVariables;
    std::string sourceText;

    const JavaClass* findClass(const std::string& className) const {
        for (const auto& c : classes) {
            if (c.name == className || c.fqn == className) return &c;
        }
        return nullptr;
    }

    const JavaClass* findClassAt(const Position& pos) const {
        for (const auto& c : classes) {
            if (c.range.contains(pos)) return &c;
        }
        return nullptr;
    }
};

} // namespace javalsp
