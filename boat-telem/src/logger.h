// TelemetryLogger — one JSON object per line (JSON Lines), one file per daemon
// launch, named with the session start timestamp (spec Section 6). Replay is a
// consumer concern; any tool can stream the file line by line.
#pragma once

#include <fstream>
#include <string>

namespace telem {

class TelemetryLogger {
public:
    // dir: where logs land (default $HOME). Returns false if the file can't be
    // created — the daemon keeps running without logging rather than dying.
    bool open(const std::string& dir, const std::string& session_iso) {
        // filesystem-safe timestamp: 2026-08-02T14:31:07.204Z -> 20260802T143107
        std::string ts;
        for (char c : session_iso) {
            if (ts.size() >= 15) break;
            if (c >= '0' && c <= '9') ts.push_back(c);
            else if (c == 'T') ts.push_back('T');
        }
        path_ = dir + "/telemetry_log_" + ts + ".jsonl";
        file_.open(path_, std::ios::app);
        return file_.is_open();
    }

    void write(const std::string& json_line) {
        if (!file_.is_open()) return;
        file_ << json_line << '\n';
        // flush every write: 20 Hz * <1 KB is trivial I/O, and a power cut on a
        // boat must not lose the tail of the session
        file_.flush();
    }

    const std::string& path() const { return path_; }

private:
    std::ofstream file_;
    std::string path_;
};

}  // namespace telem
