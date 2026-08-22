// Test harness: prints the parsed list as "label\turl" lines.
#include "style_list.hpp"
#include <cstdio>

int main(int argc, char** argv) {
    if (argc < 2)
        return 2;
    for (const auto& s : maplibre_slint::read_style_list(argv[1]))
        std::printf("%s\t%s\n", s.label.c_str(), s.url.c_str());
    return 0;
}
