"""Benchmark the llama-server command-interpreter on pi4: warm latency with
system-prompt KV caching (cache_prompt=true), which a persistent server keeps
across requests — unlike llama-cli, which reloads the model every call."""
import json
import sys
import time
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8089"
BASE = f"http://127.0.0.1:{PORT}"
SYS = ('地図音声操作の意図をJSONのみで返す。形式: '
       '{"action":"flyto","place":"地名"} または '
       '{"action":"zoom","dir":"in"または"out"} または {"action":"none"}')
USERS = ["OKトライデント、東京を表示して", "大阪に行きたい",
         "もっと拡大して", "渋谷に移動して", "今日はいい天気ですね"]


def ask(u):
    body = json.dumps({
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": u}],
        "temperature": 0, "max_tokens": 48, "cache_prompt": True,
    }).encode()
    req = urllib.request.Request(BASE + "/v1/chat/completions", body,
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=180))
    return r["choices"][0]["message"]["content"].strip(), time.time() - t0


# wait for the model to finish loading
for _ in range(180):
    try:
        urllib.request.urlopen(BASE + "/health", timeout=2)
        break
    except Exception:
        time.sleep(1)

for i, u in enumerate(USERS):
    try:
        txt, dt = ask(u)
    except Exception as e:
        txt, dt = f"ERROR {e}", 0.0
    tag = "cold" if i == 0 else "warm"
    print(f"[{tag} {dt:5.1f}s] {u}  ->  {txt.replace(chr(10), ' ')}")
