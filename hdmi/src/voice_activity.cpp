#include "voice_activity.hpp"

namespace maplibre_slint {

bool voice_is_active(const std::string& word) {
    return word == "armed" || word == "heard" || word == "asr"
           || word == "speaking";
}

}  // namespace maplibre_slint
