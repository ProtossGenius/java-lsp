#include "mybatis/mybatis_xml.h"
#include "utils/fs_utils.h"
#include "utils/logger.h"

#include <regex>
#include <sstream>
#include <cctype>

namespace javalsp {

MyBatisXmlService& MyBatisXmlService::instance() {
    static MyBatisXmlService inst;
    return inst;
}

bool MyBatisXmlService::isMapperXml(const std::string& uri, const std::string& content) const {
    if (uri.size() >= 4 && uri.substr(uri.size() - 4) == ".xml") {
        if (content.find("<mapper") != std::string::npos) {
            return true;
        }
    }
    return false;
}

void MyBatisXmlService::removeMapperXml(const std::string& uri) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = mappersByUri_.find(uri);
    if (it != mappersByUri_.end()) {
        uriByNamespace_.erase(it->second.namespaceFqn);
        mappersByUri_.erase(it);
    }
}

void MyBatisXmlService::indexMapperXml(const std::string& uri, const std::string& content) {
    std::lock_guard<std::mutex> lock(mutex_);

    MyBatisMapper mapper;
    mapper.uri = uri;
    mapper.path = fs::uriToPath(uri);

    std::istringstream stream(content);
    std::string line;
    int lineNum = 0;

    std::regex mapperNsRe(R"raw(<mapper\s+[^>]*namespace="([^"]+)")raw");
    std::regex stmtRe(R"raw(<(select|insert|update|delete)\s+[^>]*id="([^"]+)"([^>]*))raw");
    std::regex resultMapRe(R"raw(<resultMap\s+[^>]*id="([^"]+)"[^>]*type="([^"]+)"([^>]*))raw");
    std::regex colPropRe(R"raw(<(id|result)\s+[^>]*column="([^"]+)"[^>]*property="([^"]+)")raw");
    std::regex sqlRe(R"raw(<sql\s+[^>]*id="([^"]+)")raw");

    while (std::getline(stream, line)) {
        std::smatch m;

        // <mapper namespace="...">
        if (std::regex_search(line, m, mapperNsRe)) {
            mapper.namespaceFqn = m[1].str();
            int col = static_cast<int>(m.position(1));
            int len = static_cast<int>(m[1].length());
            mapper.namespaceRange = Range{{lineNum, col}, {lineNum, col + len}};
            uriByNamespace_[mapper.namespaceFqn] = uri;
        }

        // <select|insert|update|delete id="...">
        if (std::regex_search(line, m, stmtRe)) {
            MyBatisStatement stmt;
            stmt.tag = m[1].str();
            stmt.id = m[2].str();
            int col = static_cast<int>(m.position(2));
            int len = static_cast<int>(m[2].length());
            stmt.idRange = Range{{lineNum, col}, {lineNum, col + len}};
            stmt.range = Range{{lineNum, 0}, {lineNum, static_cast<int>(line.size())}};

            std::string rest = m[3].str();
            std::regex rmAttrRe(R"raw(resultMap="([^"]+)")raw");
            std::regex rtAttrRe(R"raw(resultType="([^"]+)")raw");
            std::regex ptAttrRe(R"raw(parameterType="([^"]+)")raw");
            std::smatch sm;
            if (std::regex_search(rest, sm, rmAttrRe)) stmt.resultMap = sm[1].str();
            if (std::regex_search(rest, sm, rtAttrRe)) stmt.resultType = sm[1].str();
            if (std::regex_search(rest, sm, ptAttrRe)) stmt.parameterType = sm[1].str();

            mapper.statements.push_back(stmt);
        }

        // <resultMap id="..." type="...">
        if (std::regex_search(line, m, resultMapRe)) {
            MyBatisResultMap rm;
            rm.id = m[1].str();
            rm.type = m[2].str();
            int col = static_cast<int>(m.position(1));
            int len = static_cast<int>(m[1].length());
            rm.idRange = Range{{lineNum, col}, {lineNum, col + len}};
            rm.range = Range{{lineNum, 0}, {lineNum, static_cast<int>(line.size())}};
            if (mapper.modelFqn.empty()) {
                mapper.modelFqn = rm.type;
            }
            mapper.resultMaps.push_back(rm);
        }

        // <result column="..." property="..."/>
        if (std::regex_search(line, m, colPropRe)) {
            std::string colName = m[2].str();
            std::string propName = m[3].str();
            if (!mapper.resultMaps.empty()) {
                auto& rm = mapper.resultMaps.back();
                rm.mappings.push_back({colName, propName});
                int propCol = static_cast<int>(m.position(3));
                rm.propertyRanges[propName] = Range{{lineNum, propCol}, {lineNum, propCol + static_cast<int>(propName.size())}};
                int colCol = static_cast<int>(m.position(2));
                rm.columnRanges[colName] = Range{{lineNum, colCol}, {lineNum, colCol + static_cast<int>(colName.size())}};
            }
        }

        // <sql id="...">
        if (std::regex_search(line, m, sqlRe)) {
            MyBatisSql s;
            s.id = m[1].str();
            int col = static_cast<int>(m.position(1));
            int len = static_cast<int>(m[1].length());
            s.idRange = Range{{lineNum, col}, {lineNum, col + len}};
            s.range = Range{{lineNum, 0}, {lineNum, static_cast<int>(line.size())}};
            mapper.sqls.push_back(s);
        }

        lineNum++;
    }

    mappersByUri_[uri] = mapper;
    LOG_INFO("Indexed MyBatis mapper: " + mapper.namespaceFqn + " (" +
             std::to_string(mapper.statements.size()) + " statements, " +
             std::to_string(mapper.resultMaps.size()) + " resultMaps)");
}

const MyBatisMapper* MyBatisXmlService::findMapperByUri(const std::string& uri) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = mappersByUri_.find(uri);
    if (it != mappersByUri_.end()) return &it->second;
    return nullptr;
}

const MyBatisMapper* MyBatisXmlService::findMapperByNamespace(const std::string& fqn) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = uriByNamespace_.find(fqn);
    if (it != uriByNamespace_.end()) {
        auto mIt = mappersByUri_.find(it->second);
        if (mIt != mappersByUri_.end()) return &mIt->second;
    }
    return nullptr;
}

const MyBatisMapper* MyBatisXmlService::findMapperForJavaUri(const std::string& javaUri) const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::string path = fs::uriToPath(javaUri);
    size_t lastSlash = path.find_last_of('/');
    std::string fname = (lastSlash != std::string::npos) ? path.substr(lastSlash + 1) : path;
    if (fname.size() > 5 && fname.substr(fname.size() - 5) == ".java") {
        std::string base = fname.substr(0, fname.size() - 5);
        for (const auto& pair : mappersByUri_) {
            std::string xmlPath = pair.second.path;
            size_t xmlSlash = xmlPath.find_last_of('/');
            std::string xmlFname = (xmlSlash != std::string::npos) ? xmlPath.substr(xmlSlash + 1) : xmlPath;
            if (xmlFname == base + ".xml") {
                return &pair.second;
            }
        }
    }
    return nullptr;
}

std::string MyBatisXmlService::getModelFqn(const MyBatisMapper& mapper, const SymbolTable& symTab) const {
    if (!mapper.modelFqn.empty()) return mapper.modelFqn;
    for (const auto& rm : mapper.resultMaps) {
        if (!rm.type.empty()) return rm.type;
    }
    for (const auto& s : mapper.statements) {
        if (!s.resultType.empty()) return s.resultType;
        if (!s.parameterType.empty()) return s.parameterType;
    }
    return "";
}

std::string MyBatisXmlService::getTableName(const std::string& modelFqn, const SymbolTable& symTab) const {
    if (modelFqn.empty()) return "";

    const JavaClass* cls = symTab.findClassByFqn(modelFqn);
    if (!cls) {
        size_t lastDot = modelFqn.find_last_of('.');
        std::string sName = (lastDot != std::string::npos) ? modelFqn.substr(lastDot + 1) : modelFqn;
        cls = symTab.findClassByName(sName);
    }

    if (cls) {
        std::regex tableRe(R"raw(@?(?:Table|TableName)\s*\(\s*(?:(?:value|name)\s*=\s*)?\"([^\"]+)\")raw");
        for (const auto& ann : cls->annotations) {
            std::smatch m;
            if (std::regex_search(ann, m, tableRe)) {
                return m[1].str();
            }
        }

        // Default: snake_case of class name
        std::string name = cls->name;
        std::string snake;
        for (size_t i = 0; i < name.size(); ++i) {
            if (i > 0 && std::isupper(name[i])) {
                snake.push_back('_');
            }
            snake.push_back(std::tolower(name[i]));
        }
        return snake;
    }

    return "";
}

std::vector<Location> MyBatisXmlService::findDefinitions(
    const std::string& uri,
    const Position& pos,
    const std::string& content,
    const SymbolTable& symTab) {

    std::vector<Location> results;

    // Check if cursor is in Java file
    if (uri.size() >= 5 && uri.substr(uri.size() - 5) == ".java") {
        const MyBatisMapper* mapper = findMapperForJavaUri(uri);
        if (!mapper) {
            // Try by class fqn in compilation unit
            for (const auto& [fqn, cls] : symTab.getAllClasses()) {
                mapper = findMapperByNamespace(cls.fqn);
                if (mapper) break;
            }
        }

        if (mapper) {
            // Get word at cursor in Java file
            std::istringstream stream(content);
            std::string line;
            for (int i = 0; i <= pos.line; ++i) {
                if (!std::getline(stream, line)) { line = ""; break; }
            }

            // Extract word around pos.character
            int left = pos.character;
            while (left > 0 && (std::isalnum(line[left - 1]) || line[left - 1] == '_')) left--;
            int right = pos.character;
            while (right < static_cast<int>(line.size()) && (std::isalnum(line[right]) || line[right] == '_')) right++;
            std::string word = line.substr(left, right - left);

            // If on mapper interface name, jump to <mapper> tag in XML
            if (word.find("Mapper") != std::string::npos) {
                Location loc;
                loc.uri = mapper->uri;
                loc.range = mapper->namespaceRange;
                results.push_back(loc);
                return results;
            }

            // If on method name, jump to statement in XML
            for (const auto& stmt : mapper->statements) {
                if (stmt.id == word) {
                    Location loc;
                    loc.uri = mapper->uri;
                    loc.range = stmt.idRange;
                    results.push_back(loc);
                    return results;
                }
            }
        }
        return results;
    }

    // Cursor is in XML file
    const MyBatisMapper* mapper = findMapperByUri(uri);
    if (!mapper) return results;

    std::istringstream stream(content);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) { line = ""; break; }
    }

    int left = pos.character;
    while (left > 0 && (std::isalnum(line[left - 1]) || line[left - 1] == '_' || line[left - 1] == '.')) left--;
    int right = pos.character;
    while (right < static_cast<int>(line.size()) && (std::isalnum(line[right]) || line[right] == '_' || line[right] == '.')) right++;
    std::string word = line.substr(left, right - left);

    // 1. Jump from statement id to Java Mapper method
    for (const auto& stmt : mapper->statements) {
        if (stmt.id == word) {
            const JavaClass* mapperCls = symTab.findClassByFqn(mapper->namespaceFqn);
            if (mapperCls) {
                for (const auto& m : mapperCls->methods) {
                    if (m.name == word) {
                        Location loc;
                        loc.uri = fs::pathToUri(mapperCls->filePath);
                        loc.range = m.range;
                        results.push_back(loc);
                        return results;
                    }
                }
            }
        }
    }

    // 2. Jump from namespace to Java Mapper interface
    if (mapper->namespaceFqn.find(word) != std::string::npos || word == mapper->namespaceFqn) {
        const JavaClass* mapperCls = symTab.findClassByFqn(mapper->namespaceFqn);
        if (mapperCls) {
            Location loc;
            loc.uri = fs::pathToUri(mapperCls->filePath);
            loc.range = mapperCls->range;
            results.push_back(loc);
            return results;
        }
    }

    // 3. Jump from resultMap reference to <resultMap id="...">
    for (const auto& rm : mapper->resultMaps) {
        if (rm.id == word) {
            Location loc;
            loc.uri = mapper->uri;
            loc.range = rm.idRange;
            results.push_back(loc);
            return results;
        }
    }

    // 4. Jump from refid to <sql id="...">
    for (const auto& s : mapper->sqls) {
        if (s.id == word) {
            Location loc;
            loc.uri = mapper->uri;
            loc.range = s.idRange;
            results.push_back(loc);
            return results;
        }
    }

    // 5. Jump from type / property to Model class / field
    std::string modelFqn = getModelFqn(*mapper, symTab);
    const JavaClass* modelCls = symTab.findClassByFqn(modelFqn);
    if (!modelCls) {
        size_t lastDot = modelFqn.find_last_of('.');
        std::string sName = (lastDot != std::string::npos) ? modelFqn.substr(lastDot + 1) : modelFqn;
        modelCls = symTab.findClassByName(sName);
    }

    if (modelCls) {
        if (modelCls->name == word || modelCls->fqn == word) {
            Location loc;
            loc.uri = fs::pathToUri(modelCls->filePath);
            loc.range = modelCls->range;
            results.push_back(loc);
            return results;
        }
        for (const auto& f : modelCls->fields) {
            if (f.name == word) {
                Location loc;
                loc.uri = fs::pathToUri(modelCls->filePath);
                loc.range = f.range;
                results.push_back(loc);
                return results;
            }
        }
    }

    return results;
}

CompletionList MyBatisXmlService::complete(
    const std::string& uri,
    const Position& pos,
    const std::string& content,
    const SymbolTable& symTab) {

    CompletionList list;
    list.isIncomplete = false;

    const MyBatisMapper* mapper = findMapperByUri(uri);
    if (!mapper) return list;

    std::istringstream stream(content);
    std::string line;
    for (int i = 0; i <= pos.line; ++i) {
        if (!std::getline(stream, line)) { line = ""; break; }
    }

    int character = std::min(pos.character, static_cast<int>(line.size()));
    std::string prefix = line.substr(0, character);

    std::string modelFqn = getModelFqn(*mapper, symTab);
    std::string tableName = getTableName(modelFqn, symTab);

    // 1. Column completion inside column="..."
    if (prefix.rfind("column=\"") != std::string::npos || prefix.rfind("column='") != std::string::npos) {
        auto cols = MySQLService::instance().getColumns(tableName);
        if (cols.empty() && tableName != "user") {
            cols = MySQLService::instance().getColumns("user");
        }
        for (const auto& c : cols) {
            CompletionItem item;
            item.label = c.name;
            item.kind = 5; // Field
            item.detail = c.type + (c.isPrimaryKey ? " [PRI]" : "");
            list.items.push_back(item);
        }
        return list;
    }

    // 2. Property completion inside property="..."
    if (prefix.rfind("property=\"") != std::string::npos || prefix.rfind("property='") != std::string::npos) {
        const JavaClass* modelCls = symTab.findClassByFqn(modelFqn);
        if (!modelCls) {
            size_t lastDot = modelFqn.find_last_of('.');
            std::string sName = (lastDot != std::string::npos) ? modelFqn.substr(lastDot + 1) : modelFqn;
            modelCls = symTab.findClassByName(sName);
        }
        if (modelCls) {
            for (const auto& f : modelCls->fields) {
                CompletionItem item;
                item.label = f.name;
                item.kind = 5; // Field
                item.detail = f.type.toString();
                list.items.push_back(item);
            }
        }
        return list;
    }

    // 3. refid="..."
    if (prefix.rfind("refid=\"") != std::string::npos) {
        for (const auto& s : mapper->sqls) {
            CompletionItem item;
            item.label = s.id;
            item.kind = 10; // Reference
            item.detail = "SQL fragment";
            list.items.push_back(item);
        }
        return list;
    }

    // 4. resultMap="..."
    if (prefix.rfind("resultMap=\"") != std::string::npos) {
        for (const auto& rm : mapper->resultMaps) {
            CompletionItem item;
            item.label = rm.id;
            item.kind = 7; // Class / ResultMap
            item.detail = rm.type;
            list.items.push_back(item);
        }
        return list;
    }

    // 5. #{...} parameter completion
    if (prefix.rfind("#{") != std::string::npos) {
        const JavaClass* mapperCls = symTab.findClassByFqn(mapper->namespaceFqn);
        if (mapperCls) {
            for (const auto& m : mapperCls->methods) {
                for (const auto& p : m.parameters) {
                    CompletionItem item;
                    item.label = p.second;
                    item.kind = 6; // Variable
                    item.detail = p.first.toString();
                    list.items.push_back(item);

                    // If parameter is Model class (e.g. user), also add user.name, user.email
                    const JavaClass* paramCls = symTab.findClassByName(p.first.name);
                    if (paramCls) {
                        for (const auto& f : paramCls->fields) {
                            CompletionItem subItem;
                            subItem.label = p.second + "." + f.name;
                            subItem.kind = 5; // Field
                            subItem.detail = f.type.toString();
                            list.items.push_back(subItem);
                        }
                    }
                }
            }
        }
        return list;
    }

    // 6. SQL columns inside statement body
    if (line.find("select") != std::string::npos || line.find("insert") != std::string::npos ||
        line.find("where") != std::string::npos || line.find("order by") != std::string::npos) {
        auto cols = MySQLService::instance().getColumns(tableName);
        for (const auto& c : cols) {
            CompletionItem item;
            item.label = c.name;
            item.kind = 5; // Field
            item.detail = c.type;
            list.items.push_back(item);
        }
    }

    // 7. MyBatis XML tags
    std::vector<std::string> tags = {
        "select", "insert", "update", "delete", "resultMap", "id", "result",
        "sql", "include", "where", "set", "if", "choose", "when", "otherwise", "trim", "foreach", "bind"
    };
    for (const auto& t : tags) {
        CompletionItem item;
        item.label = t;
        item.kind = 14; // Keyword
        item.detail = "MyBatis tag <" + t + ">";
        list.items.push_back(item);
    }

    return list;
}

} // namespace javalsp
