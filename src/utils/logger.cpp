#include "utils/logger.h"
#include <chrono>
#include <iomanip>

namespace javalsp {

Logger& Logger::instance() {
    static Logger inst;
    return inst;
}

Logger::~Logger() {
    if (logFile_.is_open()) {
        logFile_.flush();
        logFile_.close();
    }
}

void Logger::init(const std::string& logFilePath, LogLevel minLevel) {
    std::lock_guard<std::mutex> lock(mutex_);
    minLevel_ = minLevel;
    if (!logFilePath.empty()) {
        logFile_.open(logFilePath, std::ios::out | std::ios::app);
    }
    initialized_ = true;
}

void Logger::log(LogLevel level, const std::string& message) {
    if (level < minLevel_) return;

    std::lock_guard<std::mutex> lock(mutex_);
    auto now = std::chrono::system_clock::now();
    auto in_time_t = std::chrono::system_clock::to_time_t(now);
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()) % 1000;

    std::stringstream ss;
    ss << std::put_time(std::localtime(&in_time_t), "%Y-%m-%d %H:%M:%S")
       << '.' << std::setfill('0') << std::setw(3) << ms.count() << " ";

    switch (level) {
        case LogLevel::DEBUG_LEVEL: ss << "[DEBUG] "; break;
        case LogLevel::INFO_LEVEL:  ss << "[INFO]  "; break;
        case LogLevel::WARN_LEVEL:  ss << "[WARN]  "; break;
        case LogLevel::ERROR_LEVEL: ss << "[ERROR] "; break;
    }
    ss << message << "\n";

    std::string out = ss.str();
    if (logFile_.is_open()) {
        logFile_ << out;
        logFile_.flush();
    }
    // Also log to stderr for lsp stderr capture
    std::cerr << out;
}

} // namespace javalsp
