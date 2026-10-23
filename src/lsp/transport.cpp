#include "lsp/transport.h"
#include "utils/logger.h"
#include <llvm/Support/FormatVariadic.h>
#include <llvm/Support/raw_ostream.h>

namespace javalsp {

Transport::Transport(std::istream& in, std::ostream& out)
    : in_(in), out_(out) {}

std::optional<llvm::json::Value> Transport::readMessage() {
    size_t contentLength = 0;
    std::string line;

    while (std::getline(in_, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line.empty()) {
            break; // Header section finished
        }
        const std::string clHeader = "Content-Length: ";
        if (line.rfind(clHeader, 0) == 0) {
            contentLength = std::stoull(line.substr(clHeader.length()));
        }
    }

    if (contentLength == 0 || in_.eof()) {
        return std::nullopt;
    }

    std::string buffer;
    buffer.resize(contentLength);
    in_.read(&buffer[0], contentLength);

    if (in_.gcount() != static_cast<std::streamsize>(contentLength)) {
        LOG_ERROR("Failed to read expected bytes from input stream");
        return std::nullopt;
    }

    auto parsed = llvm::json::parse(buffer);
    if (!parsed) {
        LOG_ERROR("Failed to parse JSON-RPC message: " + llvm::toString(parsed.takeError()));
        return std::nullopt;
    }

    return std::move(*parsed);
}

void Transport::writeMessage(const llvm::json::Value& msg) {
    std::string payload;
    llvm::raw_string_ostream os(payload);
    os << msg;
    os.flush();

    std::lock_guard<std::mutex> lock(writeMutex_);
    out_ << "Content-Length: " << payload.size() << "\r\n\r\n" << payload;
    out_.flush();
}

void Transport::writeResponse(const llvm::json::Value& id, const llvm::json::Value& result) {
    llvm::json::Object obj{
        {"jsonrpc", "2.0"},
        {"id", id},
        {"result", result}
    };
    writeMessage(llvm::json::Value(std::move(obj)));
}

void Transport::writeError(const llvm::json::Value& id, int code, const std::string& message) {
    llvm::json::Object err{
        {"code", code},
        {"message", message}
    };
    llvm::json::Object obj{
        {"jsonrpc", "2.0"},
        {"id", id},
        {"error", std::move(err)}
    };
    writeMessage(llvm::json::Value(std::move(obj)));
}

void Transport::writeNotification(const std::string& method, const llvm::json::Value& params) {
    llvm::json::Object obj{
        {"jsonrpc", "2.0"},
        {"method", method},
        {"params", params}
    };
    writeMessage(llvm::json::Value(std::move(obj)));
}

} // namespace javalsp
