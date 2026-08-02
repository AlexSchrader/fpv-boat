// Small self-contained utilities: ISO-8601 timestamps, SHA-1 + base64 (needed
// only for the WebSocket handshake). No external dependencies by design — this
// has to build fast and clean on a Pi Zero 2 W.
#pragma once

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <string>

namespace util {

// "2026-08-02T14:31:07.204Z"
inline std::string iso8601_now() {
    using namespace std::chrono;
    const auto now = system_clock::now();
    const auto ms = duration_cast<milliseconds>(now.time_since_epoch()) % 1000;
    const std::time_t t = system_clock::to_time_t(now);
    std::tm tm{};
    gmtime_r(&t, &tm);
    char buf[48];
    std::snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
                  tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
                  tm.tm_hour, tm.tm_min, tm.tm_sec, static_cast<int>(ms.count()));
    return buf;
}

// Compact SHA-1 (public-domain construction) — used solely for the RFC 6455
// Sec-WebSocket-Accept handshake, not for anything security-critical.
inline void sha1(const uint8_t* data, size_t len, uint8_t out[20]) {
    uint32_t h[5] = {0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0};
    const uint64_t total_bits = static_cast<uint64_t>(len) * 8;
    // message + 0x80 pad + zeros + 8-byte length, in 64-byte blocks
    size_t padded = ((len + 8) / 64 + 1) * 64;
    std::string m(reinterpret_cast<const char*>(data), len);
    m.push_back('\x80');
    m.resize(padded, '\0');
    for (int i = 0; i < 8; i++)
        m[padded - 1 - i] = static_cast<char>((total_bits >> (8 * i)) & 0xFF);

    auto rol = [](uint32_t v, int s) { return (v << s) | (v >> (32 - s)); };
    for (size_t block = 0; block < padded; block += 64) {
        uint32_t w[80];
        for (int i = 0; i < 16; i++)
            w[i] = (static_cast<uint8_t>(m[block + 4 * i]) << 24) |
                   (static_cast<uint8_t>(m[block + 4 * i + 1]) << 16) |
                   (static_cast<uint8_t>(m[block + 4 * i + 2]) << 8) |
                   static_cast<uint8_t>(m[block + 4 * i + 3]);
        for (int i = 16; i < 80; i++)
            w[i] = rol(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4];
        for (int i = 0; i < 80; i++) {
            uint32_t f, k;
            if (i < 20)      { f = (b & c) | ((~b) & d);         k = 0x5A827999; }
            else if (i < 40) { f = b ^ c ^ d;                    k = 0x6ED9EBA1; }
            else if (i < 60) { f = (b & c) | (b & d) | (c & d);  k = 0x8F1BBCDC; }
            else             { f = b ^ c ^ d;                    k = 0xCA62C1D6; }
            uint32_t tmp = rol(a, 5) + f + e + k + w[i];
            e = d; d = c; c = rol(b, 30); b = a; a = tmp;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e;
    }
    for (int i = 0; i < 5; i++) {
        out[4 * i] = (h[i] >> 24) & 0xFF;
        out[4 * i + 1] = (h[i] >> 16) & 0xFF;
        out[4 * i + 2] = (h[i] >> 8) & 0xFF;
        out[4 * i + 3] = h[i] & 0xFF;
    }
}

inline std::string base64(const uint8_t* data, size_t len) {
    static const char tbl[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve((len + 2) / 3 * 4);
    for (size_t i = 0; i < len; i += 3) {
        uint32_t v = data[i] << 16;
        if (i + 1 < len) v |= data[i + 1] << 8;
        if (i + 2 < len) v |= data[i + 2];
        out.push_back(tbl[(v >> 18) & 63]);
        out.push_back(tbl[(v >> 12) & 63]);
        out.push_back(i + 1 < len ? tbl[(v >> 6) & 63] : '=');
        out.push_back(i + 2 < len ? tbl[v & 63] : '=');
    }
    return out;
}

}  // namespace util
