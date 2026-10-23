#pragma once

#include <string>
#include <fstream>
#include <mutex>
#include <iostream>
#include <sstream>

namespace javalsp {

enum class LogLevel {
    DEBUG_LEVEL = 0,
    INFO_LEVEL = 1,
    WARN_LEVEL = 2,
    ERROR_LEVEL = 3
};

class Logger {
public:
    static Logger& instance();

    void init(const std::string& logFilePath, LogLevel minLevel = LogLevel::INFO_LEVEL);
    void log(LogLevel level, const std::string& message);

    void debug(const std::string& msg) { log(LogLevel::DEBUG_LEVEL, msg); }
    void info(const std::string& msg)  { log(LogLevel::INFO_LEVEL, msg); }
    void warn(const std::string& msg)  { log(LogLevel::WARN_LEVEL, msg); }
    void error(const std::string& msg) { log(LogLevel::ERROR_LEVEL, msg); }

private:
    Logger() = default;
    ~Logger();

    std::ofstream logFile_;
    std::mutex mutex_;
    LogLevel minLevel_{LogLevel::INFO_LEVEL};
    bool initialized_{false};
};

#define LOG_DEBUG(msg) javalsp::Logger::instance().debug(msg)
#define LOG_INFO(msg)  javalsp::Logger::instance().info(msg)
#define LOG_WARN(msg)  javalsp::Logger::instance().warn(msg)
#define LOG_ERROR(msg) javalsp::Logger::instance().error(msg)

} // namespace javalsp
