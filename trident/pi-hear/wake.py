"""Wake-word matching for pi-hear.

moonshine's base-ja model transcribes the katakana loanword 'トライデント'
inconsistently — トライデント / トライデン / ゲットトライデント, and sometimes
garbled (トライアイエンド, バイデンと). moonshine has no hotword/vocabulary
biasing, so we match loosely on the transcribed text instead:

  1. normalise (NFKC, strip spaces/punctuation),
  2. accept if a robust core substring ('トライデ') is present,
  3. otherwise fuzzy-match a sliding window against the full wake word.

Prefix words (OK / ハイ / ウェイクアップ / Get up) are deliberately ignored —
the user adds them mainly to wake the VAD before the keyword, so only the
keyword itself needs to match.
"""
import difflib
import re
import unicodedata

DEFAULT_WAKE = "トライデント"
DEFAULT_CORE = "トライデ"
DEFAULT_THRESHOLD = 0.6
# whisper sometimes renders トライデント in romaji (e.g. "OKTryDent") especially
# under CPU load; accept those forms too (lowercased substring match).
ROMAJI_CORES = ("trident", "tryden", "toraiden", "traiden", "torident")

_STRIP = re.compile(r"[\s　。、，．,.!?！？・「」『』…ー－]")


def normalize(text):
    """NFKC-fold (full-width ＯＫ → OK) and strip spaces/punctuation."""
    return _STRIP.sub("", unicodedata.normalize("NFKC", text))


def wake_score(text, wake=DEFAULT_WAKE, core=DEFAULT_CORE,
               threshold=DEFAULT_THRESHOLD):
    """Return (matched: bool, score: float, reason: str) for an utterance.

    score is 1.0 for a core-substring hit, else the best sliding-window
    fuzzy ratio against `wake`.
    """
    n = normalize(text)
    if not n:
        return (False, 0.0, "empty")
    if core and core in n:
        return (True, 1.0, "core")
    low = n.lower()
    for rc in ROMAJI_CORES:
        if rc in low:
            return (True, 1.0, f"romaji:{rc}")
    L = len(wake)
    best, at = 0.0, ""
    # try windows a little shorter/longer than the wake word for slack
    for w_len in (L, L - 1, L + 1):
        if w_len < 2:
            continue
        for i in range(0, max(1, len(n) - w_len + 1)):
            window = n[i:i + w_len]
            r = difflib.SequenceMatcher(None, window, wake).ratio()
            if r > best:
                best, at = r, window
    return (best >= threshold, best, f"fuzzy:{at}={best:.2f}")
