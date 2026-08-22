#!/usr/bin/env python3
"""Romaji + edit-distance matching for pi-hear wake word and place names.

whisper-base mis-hears Japanese surface forms wildly (札幌→サッポロ, 沖縄→お気な,
トライデント→トライ弦) but the *reading* is stable. So we romanise both the ASR
text and the targets (pykakasi) and match by normalised Levenshtein over a
sliding window. This collapses kanji/katakana/hiragana variation and absorbs
phonetic mis-hearings, instead of patching each garble by hand.
"""
import functools
import re

import pykakasi

_kks = pykakasi.kakasi()

# Romaji of each target. Place -> (pi-flyto key, spoken JA name, spoken EN name).
#
# English needs no separate targets: romanising both sides collapses it into
# the same strings. "Hiroshima" said in English and 広島 said in Japanese both
# reduce to "hiroshima", and "Trident" lands 0.3 from "toraidento", inside the
# wake threshold that was already loose enough for Japanese mis-hearings. The
# only thing English actually requires is somewhere to keep the name to say
# back.
# Japanese: edit distance over the reading. The wake word comes back mangled
# (トライ弦, ポキプライ, 大きくらいでと) but always as some attempt at the same
# sounds, which distance absorbs.
WAKE_ROMAJI = "toraidento"
WAKE_THRESH = 0.45


# English: a prefix rule, not a distance. whisper-base does not return a
# mangled "Trident" -- it returns ordinary English words: "try it", "try that",
# "try them". No tolerance separates those from "let me try it again"; every
# candidate that caught the real wakes also caught plain speech.
#
# What separates them is the "OK". Every wake observed on the device had it and
# no non-wake did, so match on the anchor instead: "ok" (or "okay") immediately
# followed by a word starting "tr". Adjacency is the whole point -- "ok" and
# "tr" occurring separately is just English.
#
# Measured against everything whisper-base actually returned here: 8 of 9 real
# wakes fire (the ninth lost its "OK" and is indistinguishable from "I try
# them"), and 14 of 15 ordinary phrases stay quiet (the exception being someone
# saying "okay, try this one", which is rare enough to accept).
ENGLISH_WAKE_RE = re.compile(r"o+k+(ay)?tr")
PLACE_THRESH = 0.34         # tighter than the wake word: place romaji is distinctive
SHORT_PLACE_THRESH = 0.20   # ...and tighter still for the short ones, see below
SHORT_PLACE_LEN = 8

# Both the Japanese reading and the English spelling, because they are not the
# same string and only one of them is ever an exact hit. "Kyoto" said in
# English lands exactly on "kyoto" but is two edits from the reading "kyouto";
# leaning on distance to bridge that gap means a tolerance loose enough to also
# find "kyouto" inside "let me show yOU THE next slide", which flies the map to
# Kyoto mid-sentence. Listing the spelling costs a line and closes the gap
# exactly.
PLACES = [
    ("toukyou", "tokyo", "東京", "Tokyo"),
    ("tokyo", "tokyo", "東京", "Tokyo"),
    ("oosaka", "osaka", "大阪", "Osaka"),
    ("kyouto", "kyoto", "京都", "Kyoto"),
    ("kyoto", "kyoto", "京都", "Kyoto"),
    ("sapporo", "sapporo", "札幌", "Sapporo"),
    ("fukuoka", "fukuoka", "福岡", "Fukuoka"),
    ("hiroshima", "hiroshima", "広島", "Hiroshima"),
    ("naha", "naha", "那覇", "Naha"),
    ("okinawa", "naha", "沖縄", "Okinawa"),
]


def place_thresh(rom):
    """Short readings need a tighter tolerance than long ones.

    Distance is normalised by length, so a six-letter target spends the same
    budget on two edits that a ten-letter one spends on three -- and a sliding
    window over ordinary English finds six letters by accident far more often.
    Measured: at the long names' tolerance, three of eleven ordinary English
    phrases resolved to a Japanese city.
    """
    return PLACE_THRESH if len(rom) >= SHORT_PLACE_LEN else SHORT_PLACE_THRESH


def to_romaji(s):
    """Reading, as bare letters and digits.

    Punctuation and spacing come and go at the recogniser's whim -- the same
    utterance came back as "Ok, try it." once and "Okay try it" the next -- and
    every stray comma is another edit of distance against a target that has
    none. Dropping them makes the numbers mean what they look like.
    """
    r = "".join(i["hepburn"] for i in _kks.convert(s)).lower()
    return re.sub(r"[^a-z0-9]", "", r)


@functools.lru_cache(maxsize=4096)
def _lev(a, b):
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if not la:
        return lb
    if not lb:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[lb]


def best_window_dist(hay, needle):
    """Min normalised Levenshtein of needle vs any ~len(needle) substring of hay."""
    n = len(needle)
    if not hay or n == 0:
        return 1.0
    best = 1.0
    for L in (n, n - 1, n + 1, n - 2, n + 2):
        if L < 2:
            continue
        for i in range(0, max(1, len(hay) - L + 1)):
            d = _lev(hay[i:i + L], needle) / n
            if d < best:
                best = d
    return best


def english_wake(text):
    """True when the English anchor "OK tr..." is present."""
    return bool(ENGLISH_WAKE_RE.search(to_romaji(text)))


def wake_match(text):
    """(matched, score, romaji). Either language's rule can wake the device."""
    r = to_romaji(text)
    d = best_window_dist(r, WAKE_ROMAJI)
    matched = d <= WAKE_THRESH or bool(ENGLISH_WAKE_RE.search(r))
    return (matched, round(1 - d, 2), r)


def find_place(text):
    """Return (key, spoken JA, spoken EN, dist) for the best place, or None."""
    r = to_romaji(text)
    best = None
    for rom, key, ja, en in PLACES:
        d = best_window_dist(r, rom)
        if d <= place_thresh(rom) and (best is None or d < best[3]):
            best = (key, ja, en, d)
    return best


def reply_language(text):
    """Which language to answer in, decided from what came back.

    Any kana or kanji means a Japanese decode -- whisper in Japanese mode
    transliterates English rather than leaving it in Latin, so a mixed string
    is still a Japanese decode and gets a Japanese answer. Nothing to go on
    means the device's resting language.
    """
    for ch in text:
        o = ord(ch)
        if (0x3040 <= o <= 0x30FF        # hiragana + katakana
                or 0x4E00 <= o <= 0x9FFF  # CJK ideographs
                or 0xFF66 <= o <= 0xFF9D):  # half-width katakana
            return "ja"
    return "en" if text.strip() else "ja"


def confirmation(lang, spoken_ja, spoken_en):
    """The sentence to say back, in the language that was spoken to us."""
    if lang == "en":
        return f"Okay. Showing {spoken_en}."
    return f"承知しました。{spoken_ja}を表示します。"


if __name__ == "__main__":
    import sys
    tests = [
        "OK Trident, show Hiroshima",
        "Okay Trident show me Tokyo",
        "OK Trident, Kyoto please",
        "OK トライデント サッポロを表示して",
        "OK トライデンとサッポロを表示して",
        "OK トライデントをお気なお表示して",
        "OK トライ弦と沖縄を表示して",
        "OK、Trydenと広島を表示して",
        "オーケートライデント、広島を表示して",
        "OKトライデント東京を表示して",
        "OK トライ弁当 大阪を表示して",
        "今日はいい天気ですね",
        "これはラズベリーパイです",
    ]
    for t in tests:
        wm, ws, r = wake_match(t)
        p = find_place(t)
        lang = reply_language(t)
        say = confirmation(lang, p[1], p[2]) if p else "-"
        print(f"wake={'Y' if wm else 'n'}({ws}) [{lang}] "
              f"place={p[1] if p else '-':<4} say={say}")
