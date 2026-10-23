#pragma once

#include "lsp/protocol.h"
#include "parser/java_ast.h"
#include "semantics/symbol_table.h"
#include "mybatis/mysql_service.h"

#include <string>
#include <vector>
#include <unordered_map>
#include <mutex>
#include <memory>

namespace javalsp {

struct MyBatisStatement {
    std::string tag; // select, insert, update, delete
    std::string id;
    std::string resultMap;
    std::string resultType;
    std::string parameterType;
    Range range;
    Range idRange;
};

struct MyBatisResultMap {
    std::string id;
    std::string type;
    Range range;
    Range idRange;
    std::vector<std::pair<std::string, std::string>> mappings; // column -> property
    std::unordered_map<std::string, Range> propertyRanges;
    std::unordered_map<std::string, Range> columnRanges;
};

struct MyBatisSql {
    std::string id;
    Range range;
    Range idRange;
};

struct MyBatisMapper {
    std::string uri;
    std::string path;
    std::string namespaceFqn;
    Range namespaceRange;
    std::vector<MyBatisStatement> statements;
    std::vector<MyBatisResultMap> resultMaps;
    std::vector<MyBatisSql> sqls;
    std::string modelFqn;
    std::string tableName;
};

class MyBatisXmlService {
public:
    static MyBatisXmlService& instance();

    bool isMapperXml(const std::string& uri, const std::string& content) const;
    void indexMapperXml(const std::string& uri, const std::string& content);
    void removeMapperXml(const std::string& uri);

    const MyBatisMapper* findMapperByUri(const std::string& uri) const;
    const MyBatisMapper* findMapperByNamespace(const std::string& fqn) const;
    const MyBatisMapper* findMapperForJavaUri(const std::string& javaUri) const;

    // Model class and MySQL table extraction
    std::string getModelFqn(const MyBatisMapper& mapper, const SymbolTable& symTab) const;
    std::string getTableName(const std::string& modelFqn, const SymbolTable& symTab) const;

    // LSP Definition jump (Java <-> XML and within XML)
    std::vector<Location> findDefinitions(
        const std::string& uri,
        const Position& pos,
        const std::string& content,
        const SymbolTable& symTab);

    // LSP Completion inside Mapper.xml
    CompletionList complete(
        const std::string& uri,
        const Position& pos,
        const std::string& content,
        const SymbolTable& symTab);

private:
    MyBatisXmlService() = default;

    std::unordered_map<std::string, MyBatisMapper> mappersByUri_;
    std::unordered_map<std::string, std::string> uriByNamespace_;
    mutable std::mutex mutex_;
};

} // namespace javalsp
