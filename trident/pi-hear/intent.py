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

PROMPT_HEAD = """You convert a spoken command about a map into JSON.

Allowed "intent" values:
  show_place  the user wants the map moved to a place
  show_poi    the user wants things of some kind shown on the map
  clear_poi   the user wants those things taken off the map
  unknown     anything else

Fields: "intent", and optionally "place" (a place name) and "category"
(what kind of thing, e.g. cafe, restaurant, toilet).

Answer with one JSON object and nothing else.

Examples:
  "show me the map of Hiroshima" -> {"intent":"show_place","place":"Hiroshima"}
  "show cafes on map"            -> {"intent":"show_poi","category":"cafe"}
  "remove cafes from map"        -> {"intent":"clear_poi","category":"cafe"}
  "what time is it"              -> {"intent":"unknown"}

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

_CLEAR = re.compile(r"\b(remove|clear|hide|take off|delete)\b|消し|消して|クリア|非表示")
_SHOW = re.compile(r"\b(show|add|display|put|find)\b|表示|出して|見せて|追加")
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


BIN = "/usr/local/bin/"


def for_voice(transcript):
    """What the voice loop should run, or None to stay silent.

    Rules only. The voice loop cannot spend nine seconds in the middle of an
    utterance, and a sentence that matches no rule is better left alone than
    guessed at -- this moves someone's map for them.
    """
    result = by_rule(transcript)
    if result is None:
        return None
    name = result["intent"]
    if name == "show_place" and result["place"]:
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
    """Rules if they are sure, otherwise the model, otherwise unknown."""
    quick = by_rule(transcript)
    if quick is not None:
        return quick
    if not use_model:
        return _empty()
    return parse(by_model(transcript))
