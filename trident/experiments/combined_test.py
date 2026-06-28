"""Experiment A: fold ASR-error correction INTO intent extraction, one short
JSON output. Feed base/whisper-level Japanese (possibly garbled) -> 0.5B
llama-server -> action JSON, resolving misheard proper nouns from context.
Short output => fast (warm), and avoids the over-correction/slowness of
rewriting the whole sentence."""
import json
import sys
import time
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8089"
BASE = f"http://127.0.0.1:{PORT}"

SYS = (
    "あなたは地図アプリの音声コマンド解釈器。入力は日本語ASRの書き起こしで、"
    "固有名詞が誤認識されていることがある。文脈と地名知識から正しい地名に直し、"
    "意図をJSONのみで出力する。形式: {\"action\":\"flyto\",\"place\":\"地名\"} / "
    "{\"action\":\"zoom\",\"dir\":\"in\"} (in=拡大/寄る, out=縮小/引く) / "
    "{\"action\":\"none\"}。日本の地名(広島・東京・大阪等)を優先。JSON以外は出力しない。"
)
# few-shot: teach garble-resolution + intent (different from test inputs)
FEWSHOT = [
    ("昼島に行きたい", '{"action":"flyto","place":"広島"}'),
    ("もっと寄って", '{"action":"zoom","dir":"in"}'),
    ("今のままでいいよ", '{"action":"none"}'),
]
TESTS = [
    "東京を表示して",
    "おおさかに移動",
    "許島の地図を見せて",                       # 許島 -> 広島 (garble not in few-shot)
    "オーケートライベント、東京表示して",        # wake garble + command
    "ちょっと拡大して",
    "今日はいい天気ですね",
]


def ask(text):
    msgs = [{"role": "system", "content": SYS}]
    for u, a in FEWSHOT:
        msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
    msgs.append({"role": "user", "content": text})
    body = json.dumps({"messages": msgs, "temperature": 0,
                       "max_tokens": 48, "cache_prompt": True}).encode()
    req = urllib.request.Request(BASE + "/v1/chat/completions", body,
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=120))
    return r["choices"][0]["message"]["content"].strip(), time.time() - t0


for _ in range(180):
    try:
        urllib.request.urlopen(BASE + "/health", timeout=2); break
    except Exception:
        time.sleep(1)

for g in TESTS:
    try:
        out, dt = ask(g)
    except Exception as e:
        out, dt = f"ERROR {e}", 0.0
    tag = "cold" if g == TESTS[0] else "warm"
    print(f"[{tag} {dt:4.1f}s] {g}  ->  {out.replace(chr(10), ' ')}")
