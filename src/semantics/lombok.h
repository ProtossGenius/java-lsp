#pragma once

#include "parser/java_ast.h"

namespace javalsp {

class LombokHandler {
public:
    static void processClass(JavaClass& cls);
    static JavaClass getSlf4jLoggerClass();

private:
    static void applySlf4j(JavaClass& cls);
    static void applyData(JavaClass& cls);
    static void applyGetters(JavaClass& cls);
    static void applySetters(JavaClass& cls);
    static void applyRequiredArgsConstructor(JavaClass& cls);
    static void applyAllArgsConstructor(JavaClass& cls);
    static void applyNoArgsConstructor(JavaClass& cls);
    static void applyBuilder(JavaClass& cls);

    static std::string capitalize(const std::string& str);
};

} // namespace javalsp
