"""Read an intent out of what the recogniser heard.

The recogniser returns a sentence. The map takes coordinates and categories.
This is the bridge, and it has two halves.

**Rules first.** Almost everything said to this thing is one of a handful of
sentences, and they are worth answering in a millisecond rather than a second.
The rules are not a fallback for a missing model; they are the fast path.

**Then the model**, via llama.cpp with Qwen2.5-0.5B. Everything here treats its
output as untrusted: a model this size answers with JSON inside prose, with
single quotes, with trailing commas, with intents nobody offered it, and
sometimes with an apology. All of that has to become either a known intent or
"unknown" -- never an exception, because this sits inside the voice loop and an
exception there is a deck that stops listening.
"""
import json
import os
import re
import subprocess

# The whole vocabulary. Anything else the model invents is refused: acting on a
# made-up intent is worse than admitting the sentence was not understood.
INTENTS = ("show_place", "show_poi", "clear_poi", "show_here",
           "set_language", "disconnect_net", "unknown")

# What the model is allowed to answer. set_language is deliberately not on the
# list: switching languages is exactly the mistake a 0.5B model must not be
# able to make, because the deck then stops understanding the person telling it
# to switch back. The phrasings are fixed and the rules catch them outright.
# disconnect_net is off the list for the same reason, one step further: a
# model that mishears a sentence as "cut the network" takes the deck off the
# air, and the person who has to notice that is standing in front of a screen
# that looks fine. Both of these are rules-only, and deliberately so.
MODEL_INTENTS = tuple(n for n in INTENTS
                      if n not in ("set_language", "disconnect_net"))

# Category words are geo's business; asking it keeps one list, not two.
try:
    import geo as _geo
except ImportError:                                # pragma: no cover
    _geo = None

SLOTS = ("place", "category", "lang")

# Not a slot the model fills: whether the request is about where the deck is,
# rather than where the map is looking.
FLAGS = ("here",)

PROMPT_HEAD = """Convert a spoken map command into JSON.

"intent" is one of:
  show_place  move the map to a place. Any place name: a city, a country, an
              island, a building. "take me to", "go to", "where is",
              "show me the map of", "...に行きたい" all mean this.
  show_poi    show things of a kind. "where can I find", "I need a",
              "show me somewhere I can", "find me a" mean this.
  clear_poi   take those things off again. "remove", "clear", "get rid of",
              "hide" mean this.
  show_here   move the map to where the device itself is. "where am I",
              "show current location", "現在地" mean this. Unlike show_place
              it names no place, because the place is wherever we are.
  unknown     anything that is not about the map.

"place" is a place name copied from the command. Empty if none was named.
"category" is one of: cafe restaurant bar pub fastfood toilet hospital pharmacy
school bank atm fuel parking convenience supermarket hotel museum station park.
Empty if the command did not name one.

Examples:
  "show me the map of Hiroshima"  {"intent":"show_place","place":"Hiroshima","category":""}
  "take me to Reykjavik please"   {"intent":"show_place","place":"Reykjavik","category":""}
  "宮島に行きたい"                  {"intent":"show_place","place":"宮島","category":""}
  "show cafes on map"             {"intent":"show_poi","place":"","category":"cafe"}
  "where can I find a hospital"   {"intent":"show_poi","place":"","category":"hospital"}
  "I need a toilet"               {"intent":"show_poi","place":"","category":"toilet"}
  "remove cafes from map"         {"intent":"clear_poi","place":"","category":"cafe"}
  "get rid of everything"         {"intent":"clear_poi","place":"","category":""}
  "where am I"                    {"intent":"show_here","place":"","category":""}
  "show current location"         {"intent":"show_here","place":"","category":""}
  "what time is it"               {"intent":"unknown","place":"","category":""}

Command:
"""

PROMPT_TAIL = """
JSON:"""


def build_prompt(transcript):
    """Wrap the transcript in the instructions.

    The transcript is untrusted -- it is whatever was said near the microphone,
    and a person can say "ignore that". It goes in as a quoted line between the
    instructions and the tail, so that no matter what it contains, the prompt
    still ends where the instructions say it ends.
    """
    one_line = " ".join(str(transcript).split())
    return PROMPT_HEAD + json.dumps(one_line, ensure_ascii=False) + PROMPT_TAIL


# What the deck says as it switches -- in the language it is switching *to*,
# not the one being left. The last thing heard should match what it is about to
# expect, or the person answers in the wrong language.
_LANG_REPLY = {"ja": "日本語モードにします。", "en": "Switching to English."}


def reply_for_language(lang):
    return _LANG_REPLY.get(lang, _LANG_REPLY["ja"])


# Said before the network goes down, so it has to carry the one fact that
# matters: this ends by itself. Somebody who does not know that is going to
# reach for the power button.
_OFFGRID_REPLY = {
    "ja": "インターネットを%d秒切断します。自動で戻ります。",
    "en": "Going off grid for %d seconds. I will come back on my own.",
}

# What the voice command asks for. Not a free number: nothing said to a
# microphone should be able to choose how long the deck is unreachable.
OFFGRID_SECONDS = 60


def reply_for_offgrid(lang, seconds=OFFGRID_SECONDS):
    return _OFFGRID_REPLY.get(lang, _OFFGRID_REPLY["ja"]) % seconds


def _empty(intent_name="unknown"):
    out = {"intent": intent_name}
    for slot in SLOTS:
        out[slot] = ""
    for flag in FLAGS:
        out[flag] = False
    return out


def _coerce(doc):
    if not isinstance(doc, dict):
        return None
    # Small models like to wrap the answer in a container.
    if "intent" not in doc:
        for value in doc.values():
            if isinstance(value, dict) and "intent" in value:
                doc = value
                break
        else:
            return None
    name = str(doc.get("intent", "")).strip().lower()
    # MODEL_INTENTS, not INTENTS: this only ever reads a model's answer, and
    # the two intents held back from it are held back here too. The grammar
    # already forbids them, but a grammar is a request and this is a check --
    # by_server falls back to an unconstrained call when llama-server is down.
    if name not in MODEL_INTENTS:
        return _empty()
    out = _empty(name)
    for slot in SLOTS:
        value = doc.get(slot, "")
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        out[slot] = "" if value is None else str(value).strip()
    # A small model fills "category" with whatever noun it liked -- "city",
    # "map", "place". Acting on one of those queries Overpass for a tag that
    # does not exist, or worse, falls back to a default and shows cafes to
    # someone who asked for a city. An unknown category is no category.
    # Moving the map somewhere is not a request to show anything on it.
    # Observed: show_place with category "cafe" for 宮島に行きたい -- the model
    # filling a field it was required to emit. Acting on it would fly there and
    # then cover it in cafes nobody asked for.
    if name == "show_place":
        out["category"] = ""
    if out["category"] and _geo is not None:
        if _geo.canonical_category(out["category"]) is None:
            out["category"] = ""
        else:
            out["category"] = _geo.canonical_category(out["category"])
    return out


def parse(text):
    """Find the JSON object in a model's answer and make it safe to act on.

    Never raises, never returns an intent that was not offered.
    """
    if not text:
        return _empty()
    # Prefer a fenced block if there is one; otherwise the first balanced-looking
    # object. Greedy to the last brace, so nested objects survive.
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    body = fenced.group(1) if fenced else text
    match = re.search(r"\{.*\}", body, re.S)
    if not match:
        return _empty()
    blob = match.group(0)
    for attempt in (blob,
                    re.sub(r",\s*([}\]])", r"\1", blob),          # trailing comma
                    re.sub(r"'", '"', re.sub(r",\s*([}\]])", r"\1", blob))):
        try:
            doc = json.loads(attempt)
        except ValueError:
            continue
        result = _coerce(doc)
        if result is not None:
            return result
    return _empty()


# --- the fast path -----------------------------------------------------------

# re.I is not decoration. The recogniser capitalises the first word of every
# utterance and punctuates the end, so "Add restaurants on map." is what
# arrives, and \badd\b does not match it. Written and tested against typed
# lower-case text, these rules matched nothing anyone actually said.
_CLEAR = re.compile(
    r"\b(remove|clear|hide|take off|get rid of|delete)\b"
    r"|消し|消して|クリア|非表示", re.I)
_SHOW = re.compile(
    r"\b(show|add|display|put|find|need|want|looking for|where is|where are"
    r"|where can i)\b|表示|出して|見せて|追加|ある\?|どこ", re.I)
# The language being switched is the one the recogniser is using, so a request
# can only be understood in the language currently in force: "Japanese mode"
# said in Japanese while listening in English comes back as noise. Each phrasing
# therefore belongs to the language doing the asking.
#
# "mode" or a switching verb is required. "show me the map of English Bay" is a
# place in Vancouver, and "what languages do you speak" is conversation.
_LANG_EN = re.compile(
    r"\b(?:language\s*mode|switch(?:\s+to)?|change\s+to|speak)\s+"
    r"(japanese|english|nihongo)\b", re.I)
# The phrase alone, with the language name lost. See _other_language.
_LANG_EN_BARE = re.compile(r"\blanguage\s*mode\b", re.I)

# A switch that failed to parse must not become somewhere to fly to. The map
# jumping to "Rangage" and "Languise" is how these attempts announced
# themselves, which is worse than nothing happening: it loses the view as well
# as the command. This one is not a transcription workaround -- it is a guard
# against the model answering "show_place" for a sentence that was plainly
# trying to do something else.
_LANG_ATTEMPT = re.compile(
    r"\bmode\b|\bswitch\b|\bspeak\b|\blangu|言語|げんご|ゲンゴ|モード", re.I)
# whisper-base does not write 言語モード in kanji. Real transcripts of the same
# spoken phrase: ゲンゴモード, げんごモード, 言語モード. The language names come
# back both ways too (英語 / エイゴ). Written against the kanji a person types,
# this matched none of them.
_LANG_JA = re.compile(
    r"(?:言語|げんご|ゲンゴ)\s*モード"          # the phrase, however it is written
    r"|(英語|エイゴ|日本語|ニホンゴ)\s*モード")   # ...or just "<language> mode"
_LANG_JA_NAME = re.compile(r"(英語|エイゴ|日本語|ニホンゴ)")

_LANG_NAMES = {"japanese": "ja", "nihongo": "ja", "日本語": "ja",
               "ニホンゴ": "ja", "english": "en", "英語": "en", "エイゴ": "en"}

# "Here" is the deck's own position, as against wherever the map happens to be
# looking. pi-poi prefers the map's last flyTo, which is right after "show me
# Yokohama" and wrong when someone means the ground under their feet.
_HERE_ONLY = re.compile(
    r"\bwhere\s+am\s+i\b"
    r"|\b(?:show|go\s+to|take\s+me\s+to)\s+(?:my|the\s+)?current\s+location\b"
    r"|\b(?:show|go\s+to|take\s+me\s+to)\s+my\s+location\b"
    r"|\btake\s+me\s+home\b"
    r"|現在地|いまどこ|今どこ|ここはどこ", re.I)
# ...and the same idea attached to a category: "cafes near me".
_HERE_NEAR = re.compile(
    r"\bnear\s*(?:me|here|by)\b|\baround\s+(?:me|here)\b"
    r"|近く|この辺|周辺|近所", re.I)

# Clearing without naming what is on the map. Nobody looking at a screenful of
# pins thinks of them by category first; they think "get this off my map".
# Requires the map, or a bare "reset"/"clear" -- "remove that" on its own is too
# easy to say by accident, and "show me the map of Reset" is a place.
_CLEAR_ALL = re.compile(
    # A bare "clear"/"reset" only as the whole utterance: anchored at the end
    # alone it matched the last word of "show me the map of Reset", which is a
    # place in New Mexico.
    r"^\s*(?:clear|reset)\s*[.!]?\s*$"
    r"|\b(?:clear|reset)\s+(?:the\s+)?(?:map|screen|pins?|markers?)\b"
    r"|(?:地図|マップ|マーカー|ピン)を?(?:クリア|リセット|消し)"
    r"|^(?:クリア|リセット)(?:して|します)?[。\.]?$"
    r"|(?:クリア|リセット)して", re.I)

# Going off the air on purpose. This is the off-grid demonstration: everything
# the deck answers with -- the map, Overpass, Nominatim, whisper, the model --
# is on the SSD, and the way to show that is to take the network away and watch
# nothing change.
#
# A network word is required. "disconnect" alone is a word people say to each
# other, and the cost of being wrong here is a deck that vanishes off the
# network, which is exactly the failure nobody in the room can debug.
# The network word is the only part of the sentence that survives. Everything
# after it does not: three attempts at「インターネットを切断して」came back as
#
#     インターネットを説断して
#     インターネットを設断して
#     インターネットを接残して
#
# せつだん is not in whisper-base's Japanese vocabulary as 切断, and it picks a
# different homophone every time. Listing the spellings is a losing game -- so
# the rule leans on the noun, which came back perfectly all three times, and
# accepts almost any verb after it. Nothing else on this deck is said about the
# internet, so the noun plus "do something to it" can only be this.
_NET_WORD = (r"(?:インターネット|インタネット|ネットワーク|ネット"
             r"|ワイファイ|Wi-?Fi|無線|回線)")

# ...except the opposite request. "繋いで" is not a command here today, but the
# day it becomes one it must not be read as its own inverse.
_NET_ON = re.compile(r"接続|繋(?:い|げ|が)|つない|つなが|オンライン")

_NET_OFF = re.compile(
    r"\b(?:disconnect|cut|kill|drop|turn\s+off|shut\s+off|switch\s+off)\s+"
    r"(?:me\s+)?(?:from\s+)?(?:the\s+)?(?:internet|net|wi-?fi|network)\b"
    r"|\bgo\s+off\s*-?\s*(?:grid|line)\b"
    r"|\boff\s*-?\s*(?:grid|line)\s+mode\b"
    # The noun, then up to a few characters of whatever the recogniser made of
    # the verb, then any of the ways a request ends.
    r"|" + _NET_WORD + r"[^。\.]{0,6}?(?:切断|遮断|停止|切って|切り|オフ|断|して|下さい|ください)"
    r"|^\s*(?:オフライン|オフグリッド)(?:モード)?(?:に(?:して|します)?)?[。\.]?\s*$",
    re.I)

# "hotels in Shinjuku" -- a category and a place in one sentence. Without this
# the place was dropped and the search ran wherever the map happened to be,
# which after "show Tokyo" is the middle of the prefecture: the answer looked
# like the deck could only think in prefectures, when in fact Nominatim resolves
# 台東区 and 浅草 perfectly well and was simply never asked.
_POI_IN_EN = re.compile(
    r"\b(?:in|near|around|at)\s+(?:the\s+)?"
    r"([A-Za-zÀ-ɏ][A-Za-zÀ-ɏ0-9'.-]*(?:[ -][A-Za-zÀ-ɏ][A-Za-zÀ-ɏ0-9'.-]*){0,3})"
    r"\s*[.!?]?\s*$", re.I)
# 「新宿のホテル」-- the place comes first and の joins them. The lookahead pins
# the match to the last の, and the run before it may not itself contain one,
# so 「東京の新宿のホテル」takes 新宿: the nearer and narrower of the two, and the
# one a person means. Without excluding の from the run it captured 東京の新宿.
_POI_NO_JA = re.compile(r"((?:(?!の)[぀-ヿ一-鿿A-Za-z0-9])+)の(?=[^の]*$)")

_PLACE_OF = re.compile(
    r"\bmap of (?:the )?(?:city of |town of )?([a-zÀ-ɏ\s'-]+)",
    re.I)
_PLACE_JA = re.compile(r"([぀-ヿ一-鿿]+?)(?:の地図|を表示|に行|へ行)")

def _find_category(text):
    if _geo is None:
        return None
    low = text.lower()
    # Longest first, so "convenience store" does not match "store" elsewhere.
    for word in sorted(_geo.CATEGORIES, key=len, reverse=True):
        if word in low:
            return _geo.canonical_category(word)
    return None


def _poi_place(text, category):
    """A place named alongside a category, or None.

    "near me" is handled by the `here` flag and never gets here, so anything
    this finds is somewhere else: a ward, a neighbourhood, a station.
    """
    m = _POI_IN_EN.search(text)
    if m:
        name = m.group(1).strip(" .")
        # "cafes in the area" and "hotels near here" name no place.
        if name.lower() not in ("area", "here", "me", "town", "city",
                                "the area", "map", "range", "view"):
            return name
    # Japanese: strip the category word first, so 「新宿のホテル」leaves 新宿の
    # and the last の is the one that joins them.
    ja = text
    if _geo is not None:
        for word in sorted(_geo.CATEGORIES, key=len, reverse=True):
            if not word.isascii() and word in ja:
                ja = ja.split(word)[0]
                break
    m = _POI_NO_JA.search(ja)
    if m:
        name = m.group(1)
        # A verb ending swept up by the noun class ("表示して") is not a place.
        if len(name) >= 2 and not _NOT_A_PLACE_JA.search(name):
            return name
    return None


# Fragments that arrive in front of の but are not somewhere: 「このへんのカフェ」
# is the `here` flag's business, and 「近くの」likewise.
_NOT_A_PLACE_JA = re.compile(r"^(?:この|その|あの|ここ|そこ|近く|周辺|付近|今|いま)")


def _other_language(lang):
    """The one that is not in use, or None if we do not know which is.

    The language name is the fragile part of the sentence: 「英語」came back as
    「絵を」and「行こう」. The structure survives that. Asking to switch while
    listening in Japanese can only mean English -- nobody asks for the language
    they are already being understood in -- so the phrase alone is enough,
    provided we know which one that is. Called from the voice loop, which does;
    from a shell, which does not, this declines rather than guessing.
    """
    return {"ja": "en", "en": "ja"}.get(lang)


def by_rule(transcript, lang=None):
    """Answer the common sentences without waking the model. None if unsure.

    `lang` is the language currently being listened in, when the caller knows.
    """
    text = " ".join(str(transcript).split())
    if not text:
        return None

    # Before the category rules: "speak English" contains no category, but
    # nothing else should get the chance to read a language name as a place.
    m = _LANG_EN.search(text)
    if m:
        lang = _LANG_NAMES.get(m.group(1).lower())
        if lang:
            out = _empty("set_language")
            out["lang"] = lang
            return out
    if _LANG_JA.search(text) or _LANG_EN_BARE.search(text):
        # The phrase and the language name arrive as separate fragments often
        # enough (ゲンゴモード + 英語) that they are matched separately. A
        # readable name wins; without one, fall back to "the other language",
        # which is the only thing the request can mean.
        n = _LANG_JA_NAME.search(text)
        want = _LANG_NAMES.get(n.group(1)) if n else _other_language(lang)
        if want:
            out = _empty("set_language")
            out["lang"] = want
            return out

    # Before the category and place rules. "cut the network" names neither, but
    # nothing downstream should get the chance to read "net" as somewhere to go.
    if _NET_OFF.search(text) and not _NET_ON.search(text):
        return _empty("disconnect_net")

    category = _find_category(text)
    here = bool(_HERE_NEAR.search(text))

    # "Where am I" on its own is a move, not a request to show things. Checked
    # before the category rules so that a stray category word in the sentence
    # cannot turn it into one.
    if not category and _HERE_ONLY.search(text):
        return _empty("show_here")

    # Before the place rules: "clear the map" contains no category and must not
    # be read as somewhere to fly to.
    if not category and _CLEAR_ALL.search(text):
        return _empty("clear_poi")

    # Order matters: "remove the cafes shown on the map" contains both verbs,
    # and the one that decides is the one asking for a change.
    if category and _CLEAR.search(text):
        out = _empty("clear_poi")
        out["category"] = category
        return out
    # "show me the map of Hotel California" contains a category word, but the
    # sentence says plainly that it is naming a place. The explicit phrasing
    # wins over a word that happens to be in the category list.
    if category and not here and _PLACE_OF.search(text):
        m = _PLACE_OF.search(text)
        out = _empty("show_place")
        out["place"] = m.group(1).strip(" .")
        return out

    poi_place = None if here else _poi_place(text, category)
    if category and (_SHOW.search(text) or here or poi_place):
        # "cafes near me" and 「台東区のカフェ」have no verb at all. A category
        # with somewhere attached -- here, or a named place -- is already a
        # request; requiring a verb dropped exactly the sentences people use
        # when they have a place in mind.
        out = _empty("show_poi")
        out["category"] = category
        out["here"] = here
        out["place"] = poi_place or ""
        return out

    m = _PLACE_OF.search(text)
    if m:
        out = _empty("show_place")
        out["place"] = m.group(1).strip(" .")
        return out
    m = _PLACE_JA.search(text)
    if m:
        out = _empty("show_place")
        out["place"] = m.group(1)
        return out
    return None


# --- the model ---------------------------------------------------------------

SERVER = os.environ.get("PI_INTENT_SERVER", "http://127.0.0.1:8080")


def server_body(transcript, n_predict=48):
    """The JSON body for llama-server's /completion.

    cache_prompt is what makes this worth running as a server at all: the
    instructions are identical every time and only the last line varies, so the
    server keeps the evaluated prefix and a request costs the generation alone.
    Measured on the deck: 3.4s for the first, 0.4s for every one after.
    """
    return json.dumps({
        "prompt": build_prompt(transcript),
        "n_predict": n_predict,
        "temperature": 0,
        "cache_prompt": True,
        "grammar": grammar(),
    })


def by_server(transcript, base=None, timeout=None):
    """Ask the resident llama-server. '' if it is not there or does not answer."""
    import urllib.error
    import urllib.request
    url = (base or SERVER).rstrip("/") + "/completion"
    req = urllib.request.Request(
        url, data=server_body(transcript).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            return json.load(r).get("content", "")
    except Exception:                              # noqa: BLE001
        # Not running, still loading (503), or slow. The caller falls back.
        return ""


LLAMA = os.environ.get("PI_INTENT_LLAMA",
                       os.path.expanduser("~/src/llama.cpp/build/bin/llama-cli"))
MODEL = os.environ.get(
    "PI_INTENT_MODEL",
    os.path.expanduser("~/src/llama.cpp/models/qwen2.5-0.5b-instruct-q4_k_m.gguf"))
TIMEOUT = float(os.environ.get("PI_INTENT_TIMEOUT", "20"))


def answer_only(output):
    """The part of llama-cli's output that is the answer.

    It prints a banner, a list of slash commands, and the whole prompt echoed
    back before the completion -- and the prompt contains worked examples in
    exactly the format being looked for. Reading the output as a whole finds
    the first example and returns it for every sentence, which presents as a
    model that has learned to say one thing.

    The prompt ends with the marker, so everything after its last occurrence is
    the model's own words.
    """
    marker = PROMPT_TAIL.strip()
    idx = output.rfind(marker)
    return output[idx + len(marker):] if idx >= 0 else output


def by_model(transcript, llama=None, model=None, timeout=None):
    """Ask llama.cpp. Returns the answer text, or '' if it could not be asked.

    -st and --no-conversation are both needed: with neither, this build opens
    an interactive session, prints "> " and waits for a person who is not
    there, and the only symptom is that every call takes exactly the timeout.
    stdin is closed for the same reason.
    """
    cmd = [llama or LLAMA, "-m", model or MODEL,
           "-p", build_prompt(transcript),
           "-n", "48", "--temp", "0",
           "--no-conversation", "-st", "--no-warmup"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL,
                              timeout=timeout or TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return answer_only(proc.stdout or "")


def _canonical_categories():
    """The ASCII names, once each, in a stable order.

    From geo, so there is one list of categories rather than two that drift.
    Sorted because the grammar goes into every request and the server caches on
    the prompt; a set's iteration order would invalidate that cache.
    """
    if _geo is None:                               # pragma: no cover
        return ()
    names = {_geo.canonical_category(w) for w in _geo.CATEGORIES}
    return tuple(sorted(n for n in names if n))


def grammar():
    """A GBNF grammar constraining the intent, and requiring all three fields.

    Measured on the deck with Qwen2.5-0.5B, over ten sentences the rules do not
    cover:

        richer prompt, no grammar          1/10
        original prompt, no grammar        4/10
        richer prompt + this grammar       7/10

    Neither half works alone. Without the grammar the longer prompt makes the
    model waffle and everything comes back as unknown; with it, the model has to
    commit to one of four words and the prompt's mapping starts to matter.

    Enumerating the categories here as well measured *worse* -- 2/7 against 3/7.
    Once the field was open, being forced to choose from a list made the model
    fill it with a plausible wrong answer instead of leaving it empty. So the
    slots are free text and Python checks the category afterwards, where an
    unknown one can become no category rather than a nearby one.

    Requiring all three fields rather than making them optional is the same
    lesson: an optional field is an invitation to skip the hard part.
    """
    intents = " | ".join('"\\"%s\\""' % n for n in MODEL_INTENTS)
    return (
        'root ::= "{" ws "\\"intent\\"" ws ":" ws intent "," '
        'ws "\\"place\\"" ws ":" ws string "," '
        'ws "\\"category\\"" ws ":" ws string ws "}"\n'
        'intent ::= %s\n'
        'string ::= "\\"" [^"\\\\]* "\\""\n'
        'ws ::= [ \t\n]*\n' % intents
    )


BIN = "/usr/local/bin/"


# How long the voice loop will wait for the model. The resident server answers
# in about 0.8s; anything much beyond that means it is loading or gone, and a
# person standing in front of the map should get silence rather than a pause.
VOICE_TIMEOUT = 5.0

# At least this many letters or digits before it is worth asking Nominatim.
_NAME_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)


def _is_a_name(place):
    """Whether a place slot holds something worth looking up.

    Real: whisper returned "Take me to..." for a sentence that trailed off, and
    the model answered show_place with place "...". Asking a geocoder for "..."
    can only waste the time it takes to fail.
    """
    return bool(place) and bool(_NAME_RE.search(place))


def for_voice(transcript, lang=None):
    """What the voice loop should run, or None to stay silent.

    Rules first -- the phrases people actually use should not pay for a round
    trip. Then the resident model, which costs about 0.8s now that it is not
    reloading itself on every call; before that it was 5.6s and this path had to
    be rules-only. A sentence neither can read is left alone: guessing moves
    someone's map for them.
    """
    result = by_rule(transcript, lang=lang)
    from_rule = result is not None
    if result is None:
        answer = by_server(transcript, timeout=VOICE_TIMEOUT)
        result = parse(answer) if answer else None
    if result is None or result["intent"] == "unknown":
        return None
    name = result["intent"]
    if (name == "show_place" and not from_rule
            and _LANG_ATTEMPT.search(transcript)):
        # The model's guess at a mangled "language mode ..." is a place name
        # every time, and acting on it throws the map across the world.
        return None
    if name == "show_place" and _is_a_name(result["place"]):
        # Pins belong to the place they were found in. Cafes shown in Hiroshima
        # stayed on screen when the map moved, which made "show Hiroshima" look
        # like it was adding cafes nobody asked for. Wherever the map goes next,
        # the last place's pins are wrong.
        return {"tool": "pi-geocode", "args": ["--fly", result["place"]],
                "timeout": 30, "intent": result, "clear_pins": True}
    if name == "show_here":
        return {"tool": "pi-here", "args": [], "timeout": 30,
                "intent": result, "clear_pins": True}
    if name == "show_poi" and result["category"]:
        args = (["--here"] if result.get("here") else []) + [result["category"]]
        # A named place goes through as pi-poi's positional argument, which
        # geocodes it and sizes the search to it. Guarded by the same name test
        # as show_place: the model answers "..." for a sentence that trailed
        # off, and looking that up can only waste the time it takes to fail.
        if not result.get("here") and _is_a_name(result.get("place")):
            args = args + result["place"].split()
        return {"tool": "pi-poi", "args": args,
                "timeout": 120, "intent": result}
    if name == "clear_poi":
        return {"tool": "pi-poi", "args": ["clear"],
                "timeout": 20, "intent": result}
    if name == "disconnect_net":
        # speak_first for a different reason than set_language: nothing is
        # about to kill this process, but the network is about to go away, and
        # the confirmation is only useful if it arrives before the thing it is
        # confirming. The timeout covers pi-net's own settling; the return is
        # scheduled inside pi-offgrid and does not depend on this call.
        return {"tool": "pi-offgrid", "args": [str(OFFGRID_SECONDS)],
                "timeout": 60, "intent": result, "speak_first": True,
                "say": reply_for_offgrid(lang or "ja")}
    if name == "set_language" and result["lang"]:
        # speak_first because taking the new language means restarting pi-hear,
        # which kills the process that would otherwise have spoken afterwards.
        # Silence here reads as a crash.
        return {"tool": "pi-lang", "args": [result["lang"]],
                "timeout": 30, "intent": result, "speak_first": True,
                "say": reply_for_language(result["lang"])}
    return None


def read(transcript, use_model=True):
    """Rules if they are sure, then the resident server, then the CLI.

    The server is tried first because it answers in under a second; the CLI is
    there for when it is not running, and costs about six.
    """
    quick = by_rule(transcript)
    if quick is not None:
        return quick
    if not use_model:
        return _empty()
    answer = by_server(transcript)
    if answer:
        return parse(answer)
    return parse(by_model(transcript))
