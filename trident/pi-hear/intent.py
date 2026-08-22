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
INTENTS = ("show_place", "show_poi", "clear_poi", "unknown")

# Category words are geo's business; asking it keeps one list, not two.
try:
    import geo as _geo
except ImportError:                                # pragma: no cover
    _geo = None

SLOTS = ("place", "category")

PROMPT_HEAD = """Convert a spoken map command into JSON.

"intent" is one of:
  show_place  move the map to a place. Any place name: a city, a country, an
              island, a building. "take me to", "go to", "where is",
              "show me the map of", "...に行きたい" all mean this.
  show_poi    show things of a kind. "where can I find", "I need a",
              "show me somewhere I can", "find me a" mean this.
  clear_poi   take those things off again. "remove", "clear", "get rid of",
              "hide" mean this.
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


def _empty(intent_name="unknown"):
    out = {"intent": intent_name}
    for slot in SLOTS:
        out[slot] = ""
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
    if name not in INTENTS:
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


def by_rule(transcript):
    """Answer the common sentences without waking the model. None if unsure."""
    text = " ".join(str(transcript).split())
    if not text:
        return None
    category = _find_category(text)

    # Order matters: "remove the cafes shown on the map" contains both verbs,
    # and the one that decides is the one asking for a change.
    if category and _CLEAR.search(text):
        out = _empty("clear_poi")
        out["category"] = category
        return out
    if category and _SHOW.search(text):
        out = _empty("show_poi")
        out["category"] = category
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
    intents = " | ".join('"\\"%s\\""' % n for n in INTENTS)
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


def for_voice(transcript):
    """What the voice loop should run, or None to stay silent.

    Rules first -- the phrases people actually use should not pay for a round
    trip. Then the resident model, which costs about 0.8s now that it is not
    reloading itself on every call; before that it was 5.6s and this path had to
    be rules-only. A sentence neither can read is left alone: guessing moves
    someone's map for them.
    """
    result = by_rule(transcript)
    if result is None:
        answer = by_server(transcript, timeout=VOICE_TIMEOUT)
        result = parse(answer) if answer else None
    if result is None or result["intent"] == "unknown":
        return None
    name = result["intent"]
    if name == "show_place" and _is_a_name(result["place"]):
        return {"tool": "pi-geocode", "args": ["--fly", result["place"]],
                "timeout": 30, "intent": result}
    if name == "show_poi" and result["category"]:
        return {"tool": "pi-poi", "args": [result["category"]],
                "timeout": 120, "intent": result}
    if name == "clear_poi":
        return {"tool": "pi-poi", "args": ["clear"],
                "timeout": 20, "intent": result}
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
