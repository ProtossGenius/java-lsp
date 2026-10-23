#pragma once

#include <string>
#include <optional>
#include <mutex>
#include <iostream>
#include <llvm/Support/JSON.h>

namespace javalsp {

class Transport {
public:
    Transport(std::istream& in = std::cin, std::ostream& out = std::cout);

    std::optional<llvm::json::Value> readMessage();
    void writeMessage(const llvm::json::Value& msg);
    void writeResponse(const llvm::json::Value& id, const llvm::json::Value& result);
    void writeError(const llvm::json::Value& id, int code, const std::string& message);
    void writeNotification(const std::string& method, const llvm::json::Value& params);

private:
    std::istream& in_;
    std::ostream& out_;
    std::mutex writeMutex_;
};

} // namespace javalsp
