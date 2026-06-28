#!/usr/bin/env python3
"""Romaji + edit-distance matching for pi-hear wake word and place names.

whisper-base mis-hears Japanese surface forms wildly (札幌→サッポロ, 沖縄→お気な,
トライデント→トライ弦) but the *reading* is stable. So we romanise both the ASR
text and the targets (pykakasi) and match by normalised Levenshtein over a
sliding window. This collapses kanji/katakana/hiragana variation and absorbs
phonetic mis-hearings, instead of patching each garble by hand.
"""
import functools

import pykakasi

_kks = pykakasi.kakasi()

# Romaji of each target. Place -> (pi-flyto key, spoken JA name).
WAKE_ROMAJI = "toraidento"
WAKE_THRESH = 0.45          # looser: wake garbles vary more
PLACE_THRESH = 0.34         # tighter: place romaji is distinctive
PLACES = [
    ("toukyou", "tokyo", "東京"),
    ("oosaka", "osaka", "大阪"),
    ("kyouto", "kyoto", "京都"),
    ("sapporo", "sapporo", "札幌"),
    ("fukuoka", "fukuoka", "福岡"),
    ("hiroshima", "hiroshima", "広島"),
    ("naha", "naha", "那覇"),
    ("okinawa", "naha", "沖縄"),
]


def to_romaji(s):
    return "".join(i["hepburn"] for i in _kks.convert(s)).lower()


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


def wake_match(text):
    r = to_romaji(text)
    d = best_window_dist(r, WAKE_ROMAJI)
    return (d <= WAKE_THRESH, round(1 - d, 2), r)


def find_place(text):
    """Return (pi-flyto key, spoken name, dist) for the best place, or None."""
    r = to_romaji(text)
    best = None
    for rom, key, spoken in PLACES:
        d = best_window_dist(r, rom)
        if d <= PLACE_THRESH and (best is None or d < best[2]):
            best = (key, spoken, d)
    return best


if __name__ == "__main__":
    import sys
    tests = [
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
        print(f"wake={'Y' if wm else 'n'}({ws}) place={p[1] if p else '-':<4} "
              f"| {r}  <= '{t}'")
