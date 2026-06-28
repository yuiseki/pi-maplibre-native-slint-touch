"""Experiment: can llama-server repair whisper-tiny's garbled ASR output?
Feed real garbled transcriptions + a domain-entity hint; print before/after."""
import json
import sys
import time
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8089"
BASE = f"http://127.0.0.1:{PORT}"
SYS = ("あなたは日本語音声認識(ASR)の誤りを直す校正器です。入力はASRの書き起こしで、"
       "固有名詞や英語由来の語が誤認識されています。話題は小型コンピュータ(ラズベリーパイ)と"
       "地理空間・地図のAI(GeoAI)。登場する固有名詞: 広島、FOSS4G、トライデント、"
       "ラズベリーパイ、Pi5、東京。文脈から最も自然で正しい日本語に修正し、修正後の文だけを出力。")

# real garbled outputs collected from whisper-tiny / moonshine on the Pi 4
GARBLED = [
    "オーケートライベント、東京表示して",
    "許しまで解制される細法時で、トラベントラすぐり以外でご形象戦について発表する予定です。",
    "音 昼島のフッスフゾージーでは私も答談するんだけど、まさにトライデントラズベリーパイで動かせないかというチャレンジについて",
    "発表するのが一瞬だし細保事だし、こういうメッショはちょっと使っていいと思うんだよね",
]


def correct(text):
    body = json.dumps({
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": text}],
        "temperature": 0, "max_tokens": 128, "cache_prompt": True,
    }).encode()
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

for g in GARBLED:
    try:
        fixed, dt = correct(g)
    except Exception as e:
        fixed, dt = f"ERROR {e}", 0.0
    print(f"GARBLED: {g}")
    print(f"FIXED  ({dt:4.1f}s): {fixed.replace(chr(10), ' ')}")
    print()
