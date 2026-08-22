#include "style_list.hpp"

#include <cstdlib>
#include <fstream>

namespace maplibre_slint {
namespace {

std::string trim(const std::string& s) {
    const auto first = s.find_first_not_of(" \t\r\n");
    if (first == std::string::npos)
        return {};
    const auto last = s.find_last_not_of(" \t\r\n");
    return s.substr(first, last - first + 1);
}

}  // namespace

std::vector<StyleEntry> read_style_list(const std::string& path) {
    std::vector<StyleEntry> out;
    std::ifstream f(path);
    if (!f)
        return out;

    std::string line;
    while (std::getline(f, line)) {
        const std::string t = trim(line);
        if (t.empty() || t[0] == '#')
            continue;
        // Split on the LAST comma: a label is free text and may well contain
        // one ("Tokyo, Japan"), while a URL never does. Splitting on the first
        // would hand half the label to the URL.
        const auto comma = t.rfind(',');
        if (comma == std::string::npos)
            continue;
        StyleEntry e{trim(t.substr(0, comma)), trim(t.substr(comma + 1))};
        // Half an entry is worse than no entry: a blank row in the dropdown,
        // or a name that loads nothing, both look like the device is broken.
        if (e.label.empty() || e.url.empty())
            continue;
        out.push_back(std::move(e));
    }
    return out;
}

std::vector<StyleEntry> find_style_list() {
    std::vector<std::string> candidates;
    if (const char* env = std::getenv("MAPLIBRE_STYLES_FILE"))
        if (env[0] != '\0')
            candidates.emplace_back(env);
    if (const char* home = std::getenv("HOME"))
        candidates.emplace_back(std::string(home) +
                                "/.config/maplibre-slint-gl/styles.csv");
    candidates.emplace_back("/etc/maplibre-slint-gl/styles.csv");

    for (const auto& p : candidates) {
        auto list = read_style_list(p);
        if (!list.empty())
            return list;
    }
    return {};
}

}  // namespace maplibre_slint
