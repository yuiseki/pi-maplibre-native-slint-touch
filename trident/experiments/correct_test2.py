"""Experiment v2: llama-server ASR correction with rich context + few-shot.

Improvements over v1: domain/conversation context, an entity glossary with
readings, and few-shot garble→correct examples (via chat messages, so
cache_prompt caches the whole prefix). Few-shot garbles are DIFFERENT from the
test garbles, to fairly test generalisation."""
import json
import sys
import time
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8089"
BASE = f"http://127.0.0.1:{PORT}"

SYS = (
    "あなたは日本語音声認識(ASR)の校正器です。これは、広島で開催されるFOSS4Gという"
    "地理空間技術の国際会議で、GeoAIシステム『トライデント』をラズベリーパイ(Pi5)で"
    "動かす発表についての会話です。ASRは固有名詞や英語由来語を誤認識します。"
    "次の語が誤認識されやすい(読み): 広島(ひろしま)、FOSS4G(フォスフォージー)、"
    "トライデント、ラズベリーパイ、Pi5(パイファイブ)、東京(とうきょう)。"
    "音の似た誤認識を、文脈から正しい語に直す。修正後の文だけを出力し、説明はしない。"
)
# few-shot: teach the mapping pattern with DIFFERENT garbles than the test set
FEWSHOT = [
    ("昼島のフォスフォジーで発表します", "広島のFOSS4Gで発表します"),
    ("トラデントをラズベリー台で動かす挑戦", "トライデントをラズベリーパイで動かす挑戦"),
    ("パイファイブのクラスターを使う", "Pi5のクラスターを使う"),
]
# held-out test garbles (real whisper-tiny outputs, NOT in the few-shot)
TESTS = [
    "オーケートライベント、東京表示して",
    "許しまで解制される細法時で、トラベントラすぐり以外でご形象戦について発表する予定です。",
    "発表するのが一瞬だし細保事だし、こういうメッショはちょっと使っていいと思うんだよね",
]


def correct(text):
    msgs = [{"role": "system", "content": SYS}]
    for u, a in FEWSHOT:
        msgs.append({"role": "user", "content": u})
        msgs.append({"role": "assistant", "content": a})
    msgs.append({"role": "user", "content": text})
    body = json.dumps({"messages": msgs, "temperature": 0,
                       "max_tokens": 128, "cache_prompt": True}).encode()
    req = urllib.request.Request(BASE + "/v1/chat/completions", body,
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=180))
    return r["choices"][0]["message"]["content"].strip(), time.time() - t0


for _ in range(180):
    try:
        urllib.request.urlopen(BASE + "/health", timeout=2); break
    except Exception:
        time.sleep(1)

for g in TESTS:
    try:
        fixed, dt = correct(g)
    except Exception as e:
        fixed, dt = f"ERROR {e}", 0.0
    print(f"GARBLED: {g}")
    print(f"FIXED  ({dt:4.1f}s): {fixed.replace(chr(10), ' ')}")
    print()
