// Minimal RFC 6455 WebSocket broadcast server — the spec's "minimal
// hand-rolled option" (Section 10). Chosen over uWebSockets/websocketpp
// because it is the only zero-dependency choice that builds instantly on a Pi
// Zero 2 W; the protocol surface we need is tiny (server->client text frames,
// no per-message compression, no request/response), so a full library buys
// nothing here.
//
// Model: an accept thread performs the HTTP upgrade handshake synchronously,
// then the client fd joins a mutex-guarded list. broadcast() frames the JSON
// once and writes it to every client; any write error drops that client.
// Incoming client data is ignored (pure broadcast stream per spec Section 7) —
// a closed/reset socket is detected via the failed write.
#pragma once

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "util.h"

namespace telem {

class WsServer {
public:
    ~WsServer() { stop(); }

    bool start(int port) {
        listen_fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
        if (listen_fd_ < 0) return false;
        int one = 1;
        setsockopt(listen_fd_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = INADDR_ANY;
        addr.sin_port = htons(static_cast<uint16_t>(port));
        if (bind(listen_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) return false;
        if (listen(listen_fd_, 4) < 0) return false;
        running_ = true;
        accept_thread_ = std::thread([this] { accept_loop(); });
        return true;
    }

    void stop() {
        running_ = false;
        if (listen_fd_ >= 0) { ::shutdown(listen_fd_, SHUT_RDWR); ::close(listen_fd_); listen_fd_ = -1; }
        if (accept_thread_.joinable()) accept_thread_.join();
        std::lock_guard<std::mutex> lock(mtx_);
        for (int fd : clients_) ::close(fd);
        clients_.clear();
    }

    size_t client_count() {
        std::lock_guard<std::mutex> lock(mtx_);
        return clients_.size();
    }

    void broadcast(const std::string& text) {
        const std::string frame = make_frame(text);
        std::lock_guard<std::mutex> lock(mtx_);
        for (auto it = clients_.begin(); it != clients_.end();) {
            const ssize_t n = ::send(*it, frame.data(), frame.size(), MSG_NOSIGNAL);
            if (n != static_cast<ssize_t>(frame.size())) {
                ::close(*it);
                it = clients_.erase(it);   // client gone — drop silently
            } else {
                ++it;
            }
        }
    }

private:
    // FIN + text opcode; server frames are unmasked. 16-bit extended length is
    // plenty (snapshots are < 1 KB).
    static std::string make_frame(const std::string& payload) {
        std::string f;
        f.push_back('\x81');
        if (payload.size() < 126) {
            f.push_back(static_cast<char>(payload.size()));
        } else {
            f.push_back(126);
            f.push_back(static_cast<char>((payload.size() >> 8) & 0xFF));
            f.push_back(static_cast<char>(payload.size() & 0xFF));
        }
        f += payload;
        return f;
    }

    void accept_loop() {
        while (running_) {
            const int fd = ::accept(listen_fd_, nullptr, nullptr);
            if (fd < 0) continue;
            timeval tv{2, 0};   // handshake must arrive promptly
            setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
            int one = 1;
            setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
            if (handshake(fd)) {
                std::lock_guard<std::mutex> lock(mtx_);
                clients_.push_back(fd);
            } else {
                ::close(fd);
            }
        }
    }

    static bool handshake(int fd) {
        std::string req;
        char buf[1024];
        while (req.find("\r\n\r\n") == std::string::npos) {
            const ssize_t n = ::recv(fd, buf, sizeof(buf), 0);
            if (n <= 0 || req.size() > 8192) return false;
            req.append(buf, static_cast<size_t>(n));
        }
        const std::string keyHdr = "Sec-WebSocket-Key:";
        const size_t kp = req.find(keyHdr);
        if (kp == std::string::npos) return false;
        size_t vs = kp + keyHdr.size();
        while (vs < req.size() && req[vs] == ' ') vs++;
        const size_t ve = req.find("\r\n", vs);
        if (ve == std::string::npos) return false;
        const std::string key = req.substr(vs, ve - vs);

        const std::string magic = key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
        uint8_t digest[20];
        util::sha1(reinterpret_cast<const uint8_t*>(magic.data()), magic.size(), digest);
        const std::string accept = util::base64(digest, 20);

        const std::string resp =
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Accept: " + accept + "\r\n\r\n";
        return ::send(fd, resp.data(), resp.size(), MSG_NOSIGNAL) ==
               static_cast<ssize_t>(resp.size());
    }

    int listen_fd_ = -1;
    std::atomic<bool> running_{false};
    std::thread accept_thread_;
    std::mutex mtx_;
    std::vector<int> clients_;
};

}  // namespace telem
