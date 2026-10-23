#include "mybatis/mysql_service.h"
#include "utils/fs_utils.h"
#include "utils/logger.h"

#include <cstdlib>
#include <sstream>
#include <array>
#include <memory>
#include <regex>

namespace javalsp {

static std::string execCommand(const std::string& cmd) {
    std::array<char, 256> buffer;
    std::string result;
    std::unique_ptr<FILE, decltype(&pclose)> pipe(popen(cmd.c_str(), "r"), pclose);
    if (!pipe) return "";
    while (fgets(buffer.data(), buffer.size(), pipe.get()) != nullptr) {
        result += buffer.data();
    }
    return result;
}

MySQLService& MySQLService::instance() {
    static MySQLService inst;
    return inst;
}

void MySQLService::setConfig(const MySQLConfig& config) {
    std::lock_guard<std::mutex> lock(mutex_);
    config_ = config;
    tableColumnsCache_.clear();
}

void MySQLService::clearCache() {
    std::lock_guard<std::mutex> lock(mutex_);
    tableColumnsCache_.clear();
}

void MySQLService::detectConfig(const std::string& workspaceRoot) {
    std::lock_guard<std::mutex> lock(mutex_);

    // 1. Environment variables
    const char* envHost = std::getenv("MYSQL_HOST");
    const char* envPort = std::getenv("MYSQL_PORT");
    const char* envUser = std::getenv("MYSQL_USER");
    const char* envPass = std::getenv("MYSQL_PASSWORD");
    const char* envDb = std::getenv("MYSQL_DATABASE");

    if (envHost) config_.host = envHost;
    if (envPort) config_.port = std::atoi(envPort);
    if (envUser) config_.user = envUser;
    if (envPass) config_.password = envPass;
    if (envDb) config_.database = envDb;

    // 2. Check for .java-lsp.json in workspace
    if (!workspaceRoot.empty()) {
        std::string cfgPath = workspaceRoot + "/.java-lsp.json";
        if (fs::fileExists(cfgPath)) {
            std::string content = fs::readFile(cfgPath);
            std::regex hostRe(R"raw("host"\s*:\s*"([^"]+)")raw");
            std::regex userRe(R"raw("user"\s*:\s*"([^"]+)")raw");
            std::regex passRe(R"raw("password"\s*:\s*"([^"]*)")raw");
            std::regex dbRe(R"raw("database"\s*:\s*"([^"]+)")raw");
            std::regex portRe(R"raw("port"\s*:\s*([0-9]+))raw");

            std::smatch m;
            if (std::regex_search(content, m, hostRe)) config_.host = m[1].str();
            if (std::regex_search(content, m, userRe)) config_.user = m[1].str();
            if (std::regex_search(content, m, passRe)) config_.password = m[1].str();
            if (std::regex_search(content, m, dbRe)) config_.database = m[1].str();
            if (std::regex_search(content, m, portRe)) config_.port = std::stoi(m[1].str());
        }

        // 3. Check for application.yml or application.properties
        auto ymlFiles = fs::listFilesRecursively(workspaceRoot, "application.yml");
        if (ymlFiles.empty()) {
            ymlFiles = fs::listFilesRecursively(workspaceRoot, "application.properties");
        }

        for (const auto& yml : ymlFiles) {
            std::string content = fs::readFile(yml);
            // Look for jdbc:mysql://localhost:3306/demo or similar
            std::regex jdbcRe(R"(jdbc:mysql://([^:/]+)(?::([0-9]+))?/([a-zA-Z0-9_]+))");
            std::smatch m;
            if (std::regex_search(content, m, jdbcRe)) {
                config_.host = m[1].str();
                if (m[2].matched) config_.port = std::stoi(m[2].str());
                config_.database = m[3].str();
            }
        }
    }

    LOG_INFO("MySQL configured: host=" + config_.host + " port=" + std::to_string(config_.port) +
             " db=" + config_.database + " user=" + config_.user);
}

bool MySQLService::testConnection() {
    std::string cmd = "mysql -h " + config_.host + " -P " + std::to_string(config_.port) +
                      " -u " + config_.user;
    if (!config_.password.empty()) {
        cmd += " -p" + config_.password;
    }
    cmd += " -e \"SELECT 1;\" 2>/dev/null";

    std::string res = execCommand(cmd);
    return !res.empty();
}

std::vector<MySQLColumn> MySQLService::getColumns(const std::string& tableName) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (tableName.empty()) return {};

    auto it = tableColumnsCache_.find(tableName);
    if (it != tableColumnsCache_.end()) {
        return it->second;
    }

    std::vector<MySQLColumn> cols;

    // Build query command
    std::string query = "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM information_schema.columns "
                        "WHERE (table_schema='" + config_.database + "' OR table_schema='demo') "
                        "AND table_name='" + tableName + "' ORDER BY ORDINAL_POSITION;";

    std::string cmd = "mysql -h " + config_.host + " -P " + std::to_string(config_.port) +
                      " -u " + config_.user;
    if (!config_.password.empty()) {
        cmd += " -p" + config_.password;
    }
    cmd += " -B -N -e \"" + query + "\" 2>/dev/null";

    std::string output = execCommand(cmd);
    if (output.empty()) {
        // Fallback: try default database without schema filter
        std::string fbQuery = "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_KEY FROM information_schema.columns "
                              "WHERE table_name='" + tableName + "' ORDER BY ORDINAL_POSITION;";
        std::string fbCmd = "mysql -h " + config_.host + " -P " + std::to_string(config_.port) +
                            " -u " + config_.user;
        if (!config_.password.empty()) {
            fbCmd += " -p" + config_.password;
        }
        fbCmd += " -B -N -e \"" + fbQuery + "\" 2>/dev/null";
        output = execCommand(fbCmd);
    }

    std::istringstream stream(output);
    std::string line;
    while (std::getline(stream, line)) {
        if (line.empty()) continue;
        std::istringstream lineStream(line);
        std::string colName, colType, colKey;
        if (lineStream >> colName >> colType) {
            lineStream >> colKey;
            MySQLColumn col;
            col.name = colName;
            col.type = colType;
            col.isPrimaryKey = (colKey == "PRI");
            cols.push_back(col);
        }
    }

    tableColumnsCache_[tableName] = cols;
    LOG_INFO("Fetched " + std::to_string(cols.size()) + " columns for table '" + tableName + "'");
    return cols;
}

} // namespace javalsp
