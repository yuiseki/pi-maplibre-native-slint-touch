"""Experiment v3: correct BASE-level ASR errors (minor, not tiny's garbage)
with the 0.5B model. Realistic case: base mishears ラズベリーパイ→ラズベリーバイ."""
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
    "音の似た誤認識を文脈から正しい語に直す。修正後の文だけを出力し、説明はしない。"
)
FEWSHOT = [
    ("昼島のフォスフォジーで発表します", "広島のFOSS4Gで発表します"),
    ("トラデントをラズベリー台で動かす挑戦", "トライデントをラズベリーパイで動かす挑戦"),
    ("パイファイブのクラスターを使う", "Pi5のクラスターを使う"),
]
# real whisper-BASE outputs (minor errors, not tiny garbage)
TESTS = [
    "ラズベリーバイクラスターでトライデントを動かすという発表で今作っているものとはまた別です",
    "広島で開催される細胞士で、トラベントラスベリー台でご活動を調整について発表する予定です",
    "はい、広島の話ですが、私が発表しようとしているのは、Pi5を使った。",
]


def correct(text):
    msgs = [{"role": "system", "content": SYS}]
    for u, a in FEWSHOT:
        msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
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
    print(f"IN  : {g}")
    print(f"OUT ({dt:4.1f}s): {fixed.replace(chr(10), ' ')}")
    print()
