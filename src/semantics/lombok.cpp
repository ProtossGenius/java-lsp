#include "semantics/lombok.h"
#include <cctype>

namespace javalsp {

std::string LombokHandler::capitalize(const std::string& str) {
    if (str.empty()) return str;
    std::string res = str;
    res[0] = static_cast<char>(std::toupper(static_cast<unsigned char>(res[0])));
    return res;
}

JavaClass LombokHandler::getSlf4jLoggerClass() {
    JavaClass logger;
    logger.name = "Logger";
    logger.packageName = "org.slf4j";
    logger.fqn = "org.slf4j.Logger";
    logger.kind = ClassKind::INTERFACE;

    auto makeMethod = [&](const std::string& name, const std::string& doc) {
        JavaMethod m;
        m.name = name;
        m.returnType = JavaTypeRef{"void", "void", {}, false, true};
        m.parameters.push_back({JavaTypeRef{"String", "java.lang.String"}, "format"});
        m.parameters.push_back({JavaTypeRef{"Object", "java.lang.Object", {}, true, false}, "arguments"});
        m.modifiers = {"public", "abstract"};
        m.docComment = doc;
        logger.methods.push_back(m);

        JavaMethod m2;
        m2.name = name;
        m2.returnType = JavaTypeRef{"void", "void", {}, false, true};
        m2.parameters.push_back({JavaTypeRef{"String", "java.lang.String"}, "msg"});
        m2.modifiers = {"public", "abstract"};
        m2.docComment = doc;
        logger.methods.push_back(m2);

        JavaMethod m3;
        m3.name = name;
        m3.returnType = JavaTypeRef{"void", "void", {}, false, true};
        m3.parameters.push_back({JavaTypeRef{"String", "java.lang.String"}, "msg"});
        m3.parameters.push_back({JavaTypeRef{"Throwable", "java.lang.Throwable"}, "t"});
        m3.modifiers = {"public", "abstract"};
        m3.docComment = doc;
        logger.methods.push_back(m3);
    };

    makeMethod("info", "Log a message at the INFO level.");
    makeMethod("debug", "Log a message at the DEBUG level.");
    makeMethod("warn", "Log a message at the WARN level.");
    makeMethod("error", "Log a message at the ERROR level.");
    makeMethod("trace", "Log a message at the TRACE level.");

    auto makeBoolCheck = [&](const std::string& name) {
        JavaMethod m;
        m.name = name;
        m.returnType = JavaTypeRef{"boolean", "boolean", {}, false, true};
        m.modifiers = {"public", "abstract"};
        logger.methods.push_back(m);
    };

    makeBoolCheck("isInfoEnabled");
    makeBoolCheck("isDebugEnabled");
    makeBoolCheck("isWarnEnabled");
    makeBoolCheck("isErrorEnabled");
    makeBoolCheck("isTraceEnabled");

    return logger;
}

void LombokHandler::processClass(JavaClass& cls) {
    bool hasSlf4j = cls.hasAnnotation("Slf4j");
    bool hasData = cls.hasAnnotation("Data");
    bool hasGetter = cls.hasAnnotation("Getter");
    bool hasSetter = cls.hasAnnotation("Setter");
    bool hasReqArgs = cls.hasAnnotation("RequiredArgsConstructor");
    bool hasAllArgs = cls.hasAnnotation("AllArgsConstructor");
    bool hasNoArgs = cls.hasAnnotation("NoArgsConstructor");
    bool hasBuilder = cls.hasAnnotation("Builder");
    bool hasValue = cls.hasAnnotation("Value");

    if (hasSlf4j) {
        applySlf4j(cls);
    }
    if (hasData || hasValue) {
        applyData(cls);
    }
    if (hasGetter && !hasData && !hasValue) {
        applyGetters(cls);
    }
    if (hasSetter && !hasData && !hasValue) {
        applySetters(cls);
    }
    if (hasReqArgs || hasData || hasValue) {
        applyRequiredArgsConstructor(cls);
    }
    if (hasAllArgs) {
        applyAllArgsConstructor(cls);
    }
    if (hasNoArgs) {
        applyNoArgsConstructor(cls);
    }
    if (hasBuilder) {
        applyBuilder(cls);
    }
}

void LombokHandler::applySlf4j(JavaClass& cls) {
    for (const auto& f : cls.fields) {
        if (f.name == "log") return;
    }
    JavaField logField;
    logField.name = "log";
    logField.type = JavaTypeRef{"Logger", "org.slf4j.Logger"};
    logField.modifiers = {"private", "static", "final"};
    logField.docComment = "Logger created by @Slf4j";
    cls.fields.push_back(logField);
}

void LombokHandler::applyData(JavaClass& cls) {
    applyGetters(cls);
    applySetters(cls);

    // equals, hashCode, toString
    JavaMethod toStringMethod;
    toStringMethod.name = "toString";
    toStringMethod.returnType = JavaTypeRef{"String", "java.lang.String"};
    toStringMethod.modifiers = {"public"};
    cls.methods.push_back(toStringMethod);

    JavaMethod equalsMethod;
    equalsMethod.name = "equals";
    equalsMethod.returnType = JavaTypeRef{"boolean", "boolean", {}, false, true};
    equalsMethod.parameters.push_back({JavaTypeRef{"Object", "java.lang.Object"}, "o"});
    equalsMethod.modifiers = {"public"};
    cls.methods.push_back(equalsMethod);

    JavaMethod hashCodeMethod;
    hashCodeMethod.name = "hashCode";
    hashCodeMethod.returnType = JavaTypeRef{"int", "int", {}, false, true};
    hashCodeMethod.modifiers = {"public"};
    cls.methods.push_back(hashCodeMethod);
}

void LombokHandler::applyGetters(JavaClass& cls) {
    for (const auto& f : cls.fields) {
        if (f.isStatic()) continue;
        std::string cap = capitalize(f.name);
        std::string getterName = (f.type.name == "boolean") ? ("is" + cap) : ("get" + cap);

        bool exists = false;
        for (const auto& m : cls.methods) {
            if (m.name == getterName && m.parameters.empty()) {
                exists = true;
                break;
            }
        }
        if (!exists) {
            JavaMethod m;
            m.name = getterName;
            m.returnType = f.type;
            m.modifiers = {"public"};
            m.docComment = "Getter generated by Lombok for field " + f.name;
            cls.methods.push_back(m);
        }
    }
}

void LombokHandler::applySetters(JavaClass& cls) {
    for (const auto& f : cls.fields) {
        if (f.isStatic() || f.isFinal()) continue;
        std::string cap = capitalize(f.name);
        std::string setterName = "set" + cap;

        bool exists = false;
        for (const auto& m : cls.methods) {
            if (m.name == setterName && m.parameters.size() == 1) {
                exists = true;
                break;
            }
        }
        if (!exists) {
            JavaMethod m;
            m.name = setterName;
            m.returnType = JavaTypeRef{"void", "void", {}, false, true};
            m.parameters.push_back({f.type, f.name});
            m.modifiers = {"public"};
            m.docComment = "Setter generated by Lombok for field " + f.name;
            cls.methods.push_back(m);
        }
    }
}

void LombokHandler::applyRequiredArgsConstructor(JavaClass& cls) {
    JavaMethod ctor;
    ctor.name = cls.name;
    ctor.isConstructor = true;
    ctor.modifiers = {"public"};

    for (const auto& f : cls.fields) {
        if (!f.isStatic() && f.isFinal()) {
            ctor.parameters.push_back({f.type, f.name});
        }
    }
    cls.methods.push_back(ctor);
}

void LombokHandler::applyAllArgsConstructor(JavaClass& cls) {
    JavaMethod ctor;
    ctor.name = cls.name;
    ctor.isConstructor = true;
    ctor.modifiers = {"public"};

    for (const auto& f : cls.fields) {
        if (!f.isStatic()) {
            ctor.parameters.push_back({f.type, f.name});
        }
    }
    cls.methods.push_back(ctor);
}

void LombokHandler::applyNoArgsConstructor(JavaClass& cls) {
    JavaMethod ctor;
    ctor.name = cls.name;
    ctor.isConstructor = true;
    ctor.modifiers = {"public"};
    cls.methods.push_back(ctor);
}

void LombokHandler::applyBuilder(JavaClass& cls) {
    JavaMethod builderMethod;
    builderMethod.name = "builder";
    builderMethod.returnType = JavaTypeRef{cls.name + "Builder", cls.fqn + "Builder"};
    builderMethod.modifiers = {"public", "static"};
    cls.methods.push_back(builderMethod);
}

} // namespace javalsp
