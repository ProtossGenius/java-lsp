#pragma once

#include <string>
#include <vector>
#include <unordered_map>
#include <mutex>

namespace javalsp {

struct MySQLColumn {
    std::string name;
    std::string type;
    std::string comment;
    bool isPrimaryKey = false;
};

struct MySQLConfig {
    std::string host = "localhost";
    int port = 3306;
    std::string user = "root";
    std::string password = "";
    std::string database = "demo";
};

class MySQLService {
public:
    static MySQLService& instance();

    void setConfig(const MySQLConfig& config);
    const MySQLConfig& getConfig() const { return config_; }

    // Auto-detect config from workspace (e.g. application.yml, application.properties, .java-lsp.json)
    void detectConfig(const std::string& workspaceRoot);

    // Get column names for a table (queries MySQL and caches)
    std::vector<MySQLColumn> getColumns(const std::string& tableName);

    // Test connection to MySQL
    bool testConnection();

    void clearCache();

private:
    MySQLService() = default;

    MySQLConfig config_;
    std::unordered_map<std::string, std::vector<MySQLColumn>> tableColumnsCache_;
    std::mutex mutex_;
};

} // namespace javalsp
