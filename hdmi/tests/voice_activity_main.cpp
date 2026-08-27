// Test harness: prints "1" or "0" for each word given.
#include "voice_activity.hpp"
#include <cstdio>

int main(int argc, char** argv) {
    for (int i = 1; i < argc; ++i)
        std::printf("%d\n", maplibre_slint::voice_is_active(argv[i]) ? 1 : 0);
    return 0;
}
