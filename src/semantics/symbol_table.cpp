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
    normalizedToActualFqn_.clear();
    registerJdkTypes();
}

static const JavaClass* findInClassHierarchy(const JavaClass& cls, const std::string& targetName) {
    if (cls.name == targetName || cls.fqn == targetName) return &cls;

    std::string normFqn = cls.fqn;
    std::replace(normFqn.begin(), normFqn.end(), '$', '.');
    std::string normTarget = targetName;
    std::replace(normTarget.begin(), normTarget.end(), '$', '.');

    if (normFqn == normTarget || cls.name == normTarget) return &cls;

    if (normFqn.size() > normTarget.size() &&
        normFqn.compare(normFqn.size() - normTarget.size(), normTarget.size(), normTarget) == 0 &&
        normFqn[normFqn.size() - normTarget.size() - 1] == '.') {
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

    // Check if class already exists under fqn or its dot/dollar equivalent
    const JavaClass* existing = findClassByFqn(fqn);
    if (existing) {
        // Do not overwrite rich source class with a stub without methods or filePath
        if (!existing->filePath.empty() && cls.filePath.empty()) {
            return;
        }
        if (!existing->methods.empty() && cls.methods.empty()) {
            return;
        }
        if (existing->fqn != fqn && classesByFqn_.find(existing->fqn) != classesByFqn_.end()) {
            classesByFqn_[existing->fqn] = cls;
        }
    }

    classesByFqn_[fqn] = cls;
    const JavaClass* storedCls = &classesByFqn_[fqn];
    if (!storedCls->packageName.empty()) {
        auto& pkgList = classesByPackage_[storedCls->packageName];
        bool inPkg = false;
        for (const auto* p : pkgList) {
            if (p == storedCls || p->fqn == fqn) {
                inPkg = true;
                break;
            }
        }
        if (!inPkg) {
            pkgList.push_back(storedCls);
        }
    }

    if (fqn.find('$') != std::string::npos) {
        std::string dotFqn = fqn;
        std::replace(dotFqn.begin(), dotFqn.end(), '$', '.');
        normalizedToActualFqn_[dotFqn] = fqn;
        normalizedToActualFqn_[fqn] = fqn;
    }
    if (existing && !existing->fqn.empty()) {
        normalizedToActualFqn_[existing->fqn] = fqn;
        std::string exDot = existing->fqn;
        std::replace(exDot.begin(), exDot.end(), '$', '.');
        normalizedToActualFqn_[exDot] = fqn;
    }

    auto addFqnName = [&](const std::string& name, const std::string& fqnToAdd) {
        if (name.empty()) return;
        auto& list = fqnsByName_[name];
        for (const auto& ex : list) {
            if (ex == fqnToAdd) return;
        }
        list.push_back(fqnToAdd);
    };

    addFqnName(cls.name, fqn);
    if (cls.name.find('$') != std::string::npos) {
        std::string dotName = cls.name;
        std::replace(dotName.begin(), dotName.end(), '$', '.');
        addFqnName(dotName, fqn);
        size_t lastDollar = cls.name.find_last_of('$');
        addFqnName(cls.name.substr(lastDollar + 1), fqn);
    }
    if (cls.name.find('.') != std::string::npos) {
        std::string dollarName = cls.name;
        std::replace(dollarName.begin(), dollarName.end(), '.', '$');
        addFqnName(dollarName, fqn);
        size_t lastDot = cls.name.find_last_of('.');
        addFqnName(cls.name.substr(lastDot + 1), fqn);
    }
    if (fqn.find('$') != std::string::npos) {
        size_t lastDollar = fqn.find_last_of('$');
        addFqnName(fqn.substr(lastDollar + 1), fqn);
    }

    // Recursively register inner classes
    for (const auto& inner : cls.innerClasses) {
        addClass(inner);
    }
}

const JavaClass* SymbolTable::findClassByFqn(const std::string& fqn) const {
    if (fqn.empty()) return nullptr;

    // 1. Direct lookup in classesByFqn_
    auto it = classesByFqn_.find(fqn);
    if (it != classesByFqn_.end()) return &it->second;

    // 2. Lookup via normalized FQN map
    std::string norm = fqn;
    std::replace(norm.begin(), norm.end(), '$', '.');
    auto itNorm = normalizedToActualFqn_.find(norm);
    if (itNorm != normalizedToActualFqn_.end()) {
        auto itActual = classesByFqn_.find(itNorm->second);
        if (itActual != classesByFqn_.end()) return &itActual->second;
    }

    // 3. Try norm directly in classesByFqn_
    auto itDot = classesByFqn_.find(norm);
    if (itDot != classesByFqn_.end()) return &itDot->second;

    // 4. If fqn contains '.', try replacing trailing dots from right to left with '$'
    if (fqn.find('.') != std::string::npos) {
        std::vector<size_t> dotPositions;
        for (size_t i = 0; i < fqn.size(); ++i) {
            if (fqn[i] == '.') dotPositions.push_back(i);
        }
        std::string dollarFqn = fqn;
        for (auto rit = dotPositions.rbegin(); rit != dotPositions.rend(); ++rit) {
            dollarFqn[*rit] = '$';
            auto itDollar = classesByFqn_.find(dollarFqn);
            if (itDollar != classesByFqn_.end()) return &itDollar->second;
        }
    }

    // 5. Try replacing trailing dots with '$' in norm as well (if fqn had some '$')
    if (norm != fqn && norm.find('.') != std::string::npos) {
        std::vector<size_t> dotPositions;
        for (size_t i = 0; i < norm.size(); ++i) {
            if (norm[i] == '.') dotPositions.push_back(i);
        }
        std::string dollarFqn = norm;
        for (auto rit = dotPositions.rbegin(); rit != dotPositions.rend(); ++rit) {
            dollarFqn[*rit] = '$';
            auto itDollar = classesByFqn_.find(dollarFqn);
            if (itDollar != classesByFqn_.end()) return &itDollar->second;
        }
    }

    return nullptr;
}

const JavaClass* SymbolTable::findClassByName(const std::string& simpleName) const {
    auto it = fqnsByName_.find(simpleName);
    if (it != fqnsByName_.end() && !it->second.empty()) {
        return findClassByFqn(it->second.front());
    }
    std::string norm = simpleName;
    std::replace(norm.begin(), norm.end(), '$', '.');
    if (norm != simpleName) {
        auto itNorm = fqnsByName_.find(norm);
        if (itNorm != fqnsByName_.end() && !itNorm->second.empty()) {
            return findClassByFqn(itNorm->second.front());
        }
    }
    size_t lastSep = simpleName.find_last_of(".$");
    if (lastSep != std::string::npos) {
        std::string shortName = simpleName.substr(lastSep + 1);
        auto itShort = fqnsByName_.find(shortName);
        if (itShort != fqnsByName_.end() && !itShort->second.empty()) {
            return findClassByFqn(itShort->second.front());
        }
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
    if (simpleName.find('.') != std::string::npos || simpleName.find('$') != std::string::npos) {
        auto cls = findClassByFqn(simpleName);
        if (cls) return cls;

        // Try same package (e.g. cu package + "." + simpleName for Outer.Inner)
        if (!cu.packageName.empty()) {
            cls = findClassByFqn(cu.packageName + "." + simpleName);
            if (cls) return cls;
            cls = findClassByFqn(cu.packageName + "$" + simpleName);
            if (cls) return cls;
        }

        // Try explicit imports matching the prefix (e.g. import java.util.Map -> Map.Entry)
        size_t firstSep = simpleName.find_first_of(".$");
        std::string rootName = simpleName.substr(0, firstSep);
        std::string rest = simpleName.substr(firstSep);
        for (const auto& imp : cu.imports) {
            if (imp.rfind(".*") != std::string::npos) continue;
            size_t lastSep = imp.find_last_of(".$");
            std::string impName = (lastSep != std::string::npos) ? imp.substr(lastSep + 1) : imp;
            if (impName == rootName) {
                cls = findClassByFqn(imp + rest);
                if (cls) return cls;
                if (!rest.empty() && rest.front() == '.') {
                    cls = findClassByFqn(imp + "$" + rest.substr(1));
                    if (cls) return cls;
                }
                if (!rest.empty() && rest.front() == '$') {
                    cls = findClassByFqn(imp + "." + rest.substr(1));
                    if (cls) return cls;
                }
            }
        }

        // Try wildcard imports
        for (const auto& imp : cu.imports) {
            if (imp.length() >= 2 && imp.substr(imp.length() - 2) == ".*") {
                std::string pkg = imp.substr(0, imp.length() - 2);
                cls = findClassByFqn(pkg + "." + simpleName);
                if (cls) return cls;
                cls = findClassByFqn(pkg + "$" + simpleName);
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
        cls = findClassByFqn(cu.packageName + "$" + simpleName);
        if (cls) return cls;
    }

    // 3. Explicit imports
    for (const auto& imp : cu.imports) {
        if (imp.rfind(".*") != std::string::npos) continue;
        size_t lastSep = imp.find_last_of(".$");
        std::string impName = (lastSep != std::string::npos) ? imp.substr(lastSep + 1) : imp;
        if (impName == simpleName) {
            auto cls = findClassByFqn(imp);
            if (cls) return cls;
        }
        std::string normImp = imp;
        std::replace(normImp.begin(), normImp.end(), '$', '.');
        std::string normSimple = simpleName;
        std::replace(normSimple.begin(), normSimple.end(), '$', '.');
        if (normImp == normSimple ||
            (normImp.size() > normSimple.size() &&
             normImp.compare(normImp.size() - normSimple.size(), normSimple.size(), normSimple) == 0 &&
             normImp[normImp.size() - normSimple.size() - 1] == '.')) {
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
            cls = findClassByFqn(pkg + "$" + simpleName);
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
        "Comparable", "CharSequence", "Iterable", "AutoCloseable", "Record", "Enum",
        "Override", "Deprecated", "SuppressWarnings", "SafeVarargs", "FunctionalInterface"
    };
    for (const auto& t : langTypes) {
        registerSimpleClass("java.lang", t);
    }

    std::vector<std::string> langAnnotTypes = {
        "Retention", "Target", "Documented", "Inherited", "Repeatable"
    };
    for (const auto& t : langAnnotTypes) {
        registerSimpleClass("java.lang.annotation", t);
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
