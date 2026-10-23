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
    classesByPackage_.clear();
    registerJdkTypes();
}

static const JavaClass* findInClassHierarchy(const JavaClass& cls, const std::string& targetName) {
    if (cls.name == targetName || cls.fqn == targetName) return &cls;
    if (cls.fqn.size() > targetName.size() &&
        cls.fqn.compare(cls.fqn.size() - targetName.size(), targetName.size(), targetName) == 0 &&
        cls.fqn[cls.fqn.size() - targetName.size() - 1] == '.') {
        return &cls;
    }
    for (const auto& inner : cls.innerClasses) {
        auto found = findInClassHierarchy(inner, targetName);
        if (found) return found;
    }
    return nullptr;
}

void SymbolTable::addClass(const JavaClass& cls) {
    std::string fqn = cls.fqn.empty() ? cls.name : cls.fqn;

    // Do not overwrite rich source class with a stub without methods or filePath
    auto it = classesByFqn_.find(fqn);
    if (it != classesByFqn_.end()) {
        if (!it->second.filePath.empty() && cls.filePath.empty()) {
            return;
        }
        if (!it->second.methods.empty() && cls.methods.empty()) {
            return;
        }
    }

    classesByFqn_[fqn] = cls;
    const JavaClass* storedCls = &classesByFqn_[fqn];
    if (!storedCls->packageName.empty()) {
        classesByPackage_[storedCls->packageName].push_back(storedCls);
    }
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

    // Recursively register inner classes
    for (const auto& inner : cls.innerClasses) {
        addClass(inner);
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
    // 0. Search compilation unit classes and inner classes
    for (const auto& cls : cu.classes) {
        auto found = findInClassHierarchy(cls, simpleName);
        if (found) return found;
    }

    // 1. Fully qualified already or qualified inner class?
    if (simpleName.find('.') != std::string::npos) {
        auto cls = findClassByFqn(simpleName);
        if (cls) return cls;

        // Try same package (e.g. cu package + "." + simpleName for Outer.Inner)
        if (!cu.packageName.empty()) {
            cls = findClassByFqn(cu.packageName + "." + simpleName);
            if (cls) return cls;
        }

        // Try explicit imports matching the prefix (e.g. import java.util.Map -> Map.Entry)
        size_t firstDot = simpleName.find('.');
        std::string rootName = simpleName.substr(0, firstDot);
        std::string rest = simpleName.substr(firstDot);
        for (const auto& imp : cu.imports) {
            if (imp.rfind(".*") != std::string::npos) continue;
            size_t lastDot = imp.find_last_of('.');
            std::string impName = (lastDot != std::string::npos) ? imp.substr(lastDot + 1) : imp;
            if (impName == rootName) {
                cls = findClassByFqn(imp + rest);
                if (cls) return cls;
            }
        }

        // Try wildcard imports
        for (const auto& imp : cu.imports) {
            if (imp.length() >= 2 && imp.substr(imp.length() - 2) == ".*") {
                std::string pkg = imp.substr(0, imp.length() - 2);
                cls = findClassByFqn(pkg + "." + simpleName);
                if (cls) return cls;
            }
        }

        // Try standard JDK java.lang and java.util
        cls = findClassByFqn("java.lang." + simpleName);
        if (cls) return cls;
        cls = findClassByFqn("java.util." + simpleName);
        if (cls) return cls;
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

const std::unordered_map<std::string, JavaClass>& SymbolTable::getAllClasses() const {
    return classesByFqn_;
}

std::vector<const JavaClass*> SymbolTable::getClassesInPackage(const std::string& packageName) const {
    auto it = classesByPackage_.find(packageName);
    if (it != classesByPackage_.end()) {
        return it->second;
    }
    return {};
}

std::vector<JavaClass> SymbolTable::findClassesMatchingPrefix(const std::string& prefix) const {
    std::vector<JavaClass> result;
    if (prefix.empty()) return result;
    for (const auto& pair : classesByFqn_) {
        const auto& name = pair.second.name;
        if (name.size() >= prefix.size() && name.compare(0, prefix.size(), prefix) == 0) {
            result.push_back(pair.second);
            if (result.size() >= 50) break;
        }
    }
    return result;
}

void SymbolTable::registerJdkTypes() {
    auto registerSimpleClass = [&](const std::string& pkg, const std::string& name) {
        JavaClass c;
        c.name = name;
        c.packageName = pkg;
        c.fqn = pkg + "." + name;
        c.kind = ClassKind::CLASS;
        addClass(c);
        return c;
    };

    // Standard java.lang types
    std::vector<std::string> langTypes = {
        "Long", "Integer", "Short", "Byte", "Double", "Float", "Boolean", "Character", "Number",
        "Void", "Class", "Throwable", "Exception", "RuntimeException", "IllegalArgumentException",
        "IllegalStateException", "NullPointerException", "IndexOutOfBoundsException",
        "UnsupportedOperationException", "SecurityException", "ClassCastException",
        "StringBuilder", "StringBuffer", "Math", "System", "Thread", "Runnable",
        "Comparable", "CharSequence", "Iterable", "AutoCloseable", "Record", "Enum"
    };
    for (const auto& t : langTypes) {
        registerSimpleClass("java.lang", t);
    }

    // Standard java.util types
    std::vector<std::string> utilTypes = {
        "Optional", "Map", "Set", "Collection", "Queue", "Deque", "Iterator",
        "ArrayList", "LinkedList", "HashMap", "LinkedHashMap", "TreeMap", "HashSet", "LinkedHashSet", "TreeSet",
        "Arrays", "Collections", "Objects", "UUID", "Date", "Calendar", "Random", "Scanner", "StringTokenizer", "StringJoiner", "Base64",
        "CompletableFuture", "Future", "Callable", "Function", "Predicate", "Consumer", "Supplier", "BiFunction"
    };
    for (const auto& t : utilTypes) {
        registerSimpleClass("java.util", t);
    }

    // java.io
    std::vector<std::string> ioTypes = {
        "File", "InputStream", "OutputStream", "Reader", "Writer", "BufferedReader", "BufferedWriter", "IOException"
    };
    for (const auto& t : ioTypes) {
        registerSimpleClass("java.io", t);
    }

    // java.time
    std::vector<std::string> timeTypes = {
        "LocalDate", "LocalTime", "LocalDateTime", "Instant", "Duration", "Period", "ZoneId", "ZonedDateTime"
    };
    for (const auto& t : timeTypes) {
        registerSimpleClass("java.time", t);
    }

    // Common annotations (Spring, Jakarta, Lombok, MyBatis)
    std::vector<std::pair<std::string, std::string>> annotations = {
        {"jakarta.validation.constraints", "NotNull"},
        {"jakarta.validation.constraints", "NotBlank"},
        {"jakarta.validation.constraints", "NotEmpty"},
        {"jakarta.validation.constraints", "Email"},
        {"jakarta.validation.constraints", "Min"},
        {"jakarta.validation.constraints", "Max"},
        {"jakarta.validation.constraints", "Size"},
        {"org.springframework.boot.autoconfigure", "SpringBootApplication"},
        {"org.springframework.web.bind.annotation", "RestController"},
        {"org.springframework.web.bind.annotation", "Controller"},
        {"org.springframework.web.bind.annotation", "GetMapping"},
        {"org.springframework.web.bind.annotation", "PostMapping"},
        {"org.springframework.web.bind.annotation", "PutMapping"},
        {"org.springframework.web.bind.annotation", "DeleteMapping"},
        {"org.springframework.web.bind.annotation", "RequestMapping"},
        {"org.springframework.web.bind.annotation", "PathVariable"},
        {"org.springframework.web.bind.annotation", "RequestParam"},
        {"org.springframework.web.bind.annotation", "RequestBody"},
        {"org.springframework.beans.factory.annotation", "Autowired"},
        {"org.springframework.stereotype", "Service"},
        {"org.springframework.stereotype", "Component"},
        {"org.springframework.stereotype", "Repository"},
        {"org.springframework.http", "ResponseEntity"},
        {"org.springframework.http", "HttpStatus"},
        {"org.apache.ibatis.annotations", "Param"},
        {"org.apache.ibatis.annotations", "Mapper"},
        {"org.apache.ibatis.annotations", "Select"},
        {"org.apache.ibatis.annotations", "Insert"},
        {"org.apache.ibatis.annotations", "Update"},
        {"org.apache.ibatis.annotations", "Delete"},
        {"lombok", "Data"},
        {"lombok", "Builder"},
        {"lombok", "NoArgsConstructor"},
        {"lombok", "AllArgsConstructor"},
        {"lombok", "Getter"},
        {"lombok", "Setter"},
        {"lombok", "ToString"},
        {"lombok", "EqualsAndHashCode"},
        {"lombok.extern.slf4j", "Slf4j"}
    };
    for (const auto& a : annotations) {
        registerSimpleClass(a.first, a.second);
    }
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
