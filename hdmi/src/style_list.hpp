#pragma once

#include <string>
#include <vector>

namespace maplibre_slint {

struct StyleEntry {
    std::string label;   // what the dropdown shows
    std::string url;     // what the map loads
};

/// Read a `label,url` list. Returns an empty vector when the file is missing
/// or unreadable, so callers can fall back to a built-in list.
///
/// The list used to be two parallel arrays typed into the .slint file, which
/// made pointing the device at a different server -- the whole of running it
/// off-grid -- a rebuild.
std::vector<StyleEntry> read_style_list(const std::string& path);

/// The first readable list among: $MAPLIBRE_STYLES_FILE,
/// ~/.config/maplibre-slint-gl/styles.csv, /etc/maplibre-slint-gl/styles.csv.
/// Empty when none of them exist.
std::vector<StyleEntry> find_style_list();

}  // namespace maplibre_slint
