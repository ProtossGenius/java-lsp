#include "semantics/symbol_table.h"
#include "semantics/lombok.h"
#include "utils/logger.h"

namespace javalsp {

SymbolTable& SymbolTable::instance() {
    static SymbolTable inst;
    return inst;
}

SymbolTable::SymbolTable() {
    registerJdkTypes();
}

void SymbolTable::clear() {
    classesByFqn_.clear();
    fqnsByName_.clear();
    registerJdkTypes();
}

void SymbolTable::addClass(const JavaClass& cls) {
    std::string fqn = cls.fqn.empty() ? cls.name : cls.fqn;
    classesByFqn_[fqn] = cls;
    auto& list = fqnsByName_[cls.name];
    bool exists = false;
    for (const auto& existing : list) {
        if (existing == fqn) {
            exists = true;
            break;
        }
    }
    if (!exists) {
        list.push_back(fqn);
    }
}

const JavaClass* SymbolTable::findClassByFqn(const std::string& fqn) const {
    auto it = classesByFqn_.find(fqn);
    if (it != classesByFqn_.end()) return &it->second;
    return nullptr;
}

const JavaClass* SymbolTable::findClassByName(const std::string& simpleName) const {
    auto it = fqnsByName_.find(simpleName);
    if (it != fqnsByName_.end() && !it->second.empty()) {
        return findClassByFqn(it->second.front());
    }
    return nullptr;
}

const JavaClass* SymbolTable::resolveClass(const std::string& simpleName, const JavaCompilationUnit& cu) const {
    // 1. Fully qualified already?
    if (simpleName.find('.') != std::string::npos) {
        return findClassByFqn(simpleName);
    }

    // 2. Same package
    if (!cu.packageName.empty()) {
        std::string samePkgFqn = cu.packageName + "." + simpleName;
        auto cls = findClassByFqn(samePkgFqn);
        if (cls) return cls;
    }

    // 3. Explicit imports
    for (const auto& imp : cu.imports) {
        if (imp.rfind(".*") != std::string::npos) continue;
        size_t lastDot = imp.find_last_of('.');
        std::string impName = (lastDot != std::string::npos) ? imp.substr(lastDot + 1) : imp;
        if (impName == simpleName) {
            auto cls = findClassByFqn(imp);
            if (cls) return cls;
        }
    }

    // 4. Wildcard imports
    for (const auto& imp : cu.imports) {
        if (imp.length() >= 2 && imp.substr(imp.length() - 2) == ".*") {
            std::string pkg = imp.substr(0, imp.length() - 2);
            std::string candidateFqn = pkg + "." + simpleName;
            auto cls = findClassByFqn(candidateFqn);
            if (cls) return cls;
        }
    }

    // 5. java.lang.*
    auto langCls = findClassByFqn("java.lang." + simpleName);
    if (langCls) return langCls;

    // 6. java.util.*
    auto utilCls = findClassByFqn("java.util." + simpleName);
    if (utilCls) return utilCls;

    // 7. Simple name search
    return findClassByName(simpleName);
}

std::vector<JavaClass> SymbolTable::getAllClasses() const {
    std::vector<JavaClass> result;
    for (const auto& pair : classesByFqn_) {
        result.push_back(pair.second);
    }
    return result;
}

void SymbolTable::registerJdkTypes() {
    // java.lang.Object
    {
        JavaClass obj;
        obj.name = "Object";
        obj.packageName = "java.lang";
        obj.fqn = "java.lang.Object";
        JavaMethod toStringM;
        toStringM.name = "toString";
        toStringM.returnType = JavaTypeRef{"String", "java.lang.String"};
        obj.methods.push_back(toStringM);
        JavaMethod equalsM;
        equalsM.name = "equals";
        equalsM.returnType = JavaTypeRef{"boolean", "boolean", {}, false, true};
        equalsM.parameters.push_back({JavaTypeRef{"Object", "java.lang.Object"}, "o"});
        obj.methods.push_back(equalsM);
        JavaMethod hashM;
        hashM.name = "hashCode";
        hashM.returnType = JavaTypeRef{"int", "int", {}, false, true};
        obj.methods.push_back(hashM);
        addClass(obj);
    }

    // java.lang.String
    {
        JavaClass str;
        str.name = "String";
        str.packageName = "java.lang";
        str.fqn = "java.lang.String";
        auto makeStrM = [&](const std::string& name, const std::string& ret) {
            JavaMethod m;
            m.name = name;
            m.returnType = JavaTypeRef{ret, "java.lang." + ret};
            str.methods.push_back(m);
        };
        makeStrM("length", "int");
        makeStrM("isEmpty", "boolean");
        makeStrM("toLowerCase", "String");
        makeStrM("toUpperCase", "String");
        makeStrM("trim", "String");

        JavaMethod formatM;
        formatM.name = "format";
        formatM.returnType = JavaTypeRef{"String", "java.lang.String"};
        formatM.modifiers = {"public", "static"};
        formatM.parameters.push_back({JavaTypeRef{"String", "java.lang.String"}, "format"});
        formatM.parameters.push_back({JavaTypeRef{"Object", "java.lang.Object", {}, true, false}, "args"});
        str.methods.push_back(formatM);

        JavaMethod valOf;
        valOf.name = "valueOf";
        valOf.returnType = JavaTypeRef{"String", "java.lang.String"};
        valOf.modifiers = {"public", "static"};
        valOf.parameters.push_back({JavaTypeRef{"Object", "java.lang.Object"}, "obj"});
        str.methods.push_back(valOf);

        addClass(str);
    }

    // java.util.List
    {
        JavaClass list;
        list.name = "List";
        list.packageName = "java.util";
        list.fqn = "java.util.List";
        list.kind = ClassKind::INTERFACE;

        // of(E... elements)
        JavaMethod ofM;
        ofM.name = "of";
        ofM.returnType = JavaTypeRef{"List", "java.util.List"};
        ofM.modifiers = {"public", "static"};
        ofM.parameters.push_back({JavaTypeRef{"E", "E", {}, true, false}, "elements"});
        ofM.docComment = "Returns an unmodifiable list containing arbitrary number of elements.";
        list.methods.push_back(ofM);

        // size()
        JavaMethod sizeM;
        sizeM.name = "size";
        sizeM.returnType = JavaTypeRef{"int", "int", {}, false, true};
        list.methods.push_back(sizeM);

        // isEmpty()
        JavaMethod emptyM;
        emptyM.name = "isEmpty";
        emptyM.returnType = JavaTypeRef{"boolean", "boolean", {}, false, true};
        list.methods.push_back(emptyM);

        // add(E element) -> boolean
        JavaMethod addM;
        addM.name = "add";
        addM.returnType = JavaTypeRef{"boolean", "boolean", {}, false, true};
        addM.parameters.push_back({JavaTypeRef{"E", "E"}, "e"});
        list.methods.push_back(addM);

        // get(int index) -> E
        JavaMethod getM;
        getM.name = "get";
        getM.returnType = JavaTypeRef{"E", "E"};
        getM.parameters.push_back({JavaTypeRef{"int", "int", {}, false, true}, "index"});
        list.methods.push_back(getM);

        // clear() -> void
        JavaMethod clearM;
        clearM.name = "clear";
        clearM.returnType = JavaTypeRef{"void", "void", {}, false, true};
        list.methods.push_back(clearM);

        // stream() -> Stream<E>
        JavaMethod streamM;
        streamM.name = "stream";
        streamM.returnType = JavaTypeRef{"Stream", "java.util.stream.Stream", {JavaTypeRef{"E", "E"}}};
        streamM.docComment = "Returns a sequential Stream with this collection as its source.";
        list.methods.push_back(streamM);

        addClass(list);
    }

    // java.util.stream.Stream
    {
        JavaClass stream;
        stream.name = "Stream";
        stream.packageName = "java.util.stream";
        stream.fqn = "java.util.stream.Stream";
        stream.kind = ClassKind::INTERFACE;

        // filter(Predicate<? super T>) -> Stream<T>
        JavaMethod filterM;
        filterM.name = "filter";
        filterM.returnType = JavaTypeRef{"Stream", "java.util.stream.Stream", {JavaTypeRef{"T", "T"}}};
        filterM.parameters.push_back({JavaTypeRef{"Predicate", "java.util.function.Predicate"}, "predicate"});
        stream.methods.push_back(filterM);

        // map(Function<? super T, ? extends R>) -> Stream<R>
        JavaMethod mapM;
        mapM.name = "map";
        mapM.returnType = JavaTypeRef{"Stream", "java.util.stream.Stream", {JavaTypeRef{"R", "R"}}};
        mapM.parameters.push_back({JavaTypeRef{"Function", "java.util.function.Function"}, "mapper"});
        stream.methods.push_back(mapM);

        // toList() -> List<T>
        JavaMethod toListM;
        toListM.name = "toList";
        toListM.returnType = JavaTypeRef{"List", "java.util.List", {JavaTypeRef{"T", "T"}}};
        stream.methods.push_back(toListM);

        // anyMatch(Predicate<? super T>) -> boolean
        JavaMethod anyMatchM;
        anyMatchM.name = "anyMatch";
        anyMatchM.returnType = JavaTypeRef{"boolean", "boolean", {}, false, true};
        anyMatchM.parameters.push_back({JavaTypeRef{"Predicate", "java.util.function.Predicate"}, "predicate"});
        stream.methods.push_back(anyMatchM);

        addClass(stream);
    }

    // org.slf4j.Logger
    addClass(LombokHandler::getSlf4jLoggerClass());
}

} // namespace javalsp
