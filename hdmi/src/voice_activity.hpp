// Whether a pi-hear state word means somebody is talking to the deck.
//
// The screensaver's idle clock was deliberately touch-only ("touch-to-wake, not
// voice-wake"): waking a black screen on a stray noise is worse than not waking
// it. Deferring is the other half and was missing -- a conversation with the map
// left the clock untouched, so the saver could arrive mid-sentence. Worse than
// it sounds, because pi-hear pauses itself once the saver is up: the deck stops
// listening exactly when it is being spoken to, and only a touch brings it back.
//
// Deferring is safe where waking is not. Nothing here is reachable without the
// wake word having already matched, so a passing noise cannot reach it.
#pragma once
#include <string>

namespace maplibre_slint {

// "armed"    the wake word just matched
// "heard"    an utterance was understood
// "asr"      an utterance is being transcribed
// "speaking" the deck is replying
//
// Deliberately not "listening": that is the resting state, present whenever
// pi-hear is alive, and counting it would mean the screensaver never arrives.
// Nor "muted", "paused" or "down", which are all "not in a conversation".
bool voice_is_active(const std::string& word);

}  // namespace maplibre_slint
