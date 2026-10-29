"""第二轮：gpt-5 家族枚举 + 参数透传 + 流式支持探测"""
import json
import time

import requests

from probe import BASE_URL, UA, make_jwt

CANDIDATES = [
    "gpt-5", "gpt-5-nano", "gpt-5-pro", "gpt-5-chat-latest", "gpt-5.1", "gpt-5.1-mini",
    "gpt-5.2", "gpt-5.2-mini", "gpt-5-codex", "o1", "o1-mini", "o3", "gpt-4.5-preview",
    "gpt-4o-2024-08-06", "chatgpt-4o-latest", "gpt-4o-mini-2024-07-18",
]


def hdr():
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": UA,
        "Authorization": "Bearer " + make_jwt(),
    }


print("############ 模型枚举 (endpoint=grammar) ############")
for m in CANDIDATES:
    body = {"model": m, "messages": [{"role": "user", "content": "Reply with exactly: OK"}], "user": "user"}
    t0 = time.time()
    try:
        r = requests.post(BASE_URL + "v2/grammar", headers=hdr(), json=body, timeout=120)
        dt = time.time() - t0
        d = r.json()
        if d.get("choices"):
            print(f"[OK ] {m:26s} {dt:5.2f}s model={d.get('model')} usage={d.get('usage')}")
        else:
            print(f"[-- ] {m:26s} {dt:5.2f}s {json.dumps(d.get('error'))[:150]}")
    except Exception as e:
        print(f"[ERR] {m:26s} {e}")

print("\n############ 参数透传 (gpt-5-mini) ############")
for extra in [
    {"max_tokens": 32},
    {"max_completion_tokens": 32},
    {"temperature": 0.2, "max_tokens": 32},
    {"n": 2, "max_tokens": 32},
    {"top_p": 0.5, "max_tokens": 16},
]:
    body = {"model": "gpt-5-mini", "messages": [{"role": "user", "content": "1+1=?"}], "user": "user", **extra}
    r = requests.post(BASE_URL + "v2/grammar", headers=hdr(), json=body, timeout=120)
    d = r.json()
    ok = bool(d.get("choices"))
    print(f"[{'OK' if ok else '--'}] extra={extra} -> {json.dumps(d.get('error'))[:120] if not ok else d['choices'][0]['message']['content'][:60]} usage={d.get('usage')}")

print("\n############ 流式 (stream=true) ############")
body = {"model": "gpt-5-mini", "messages": [{"role": "user", "content": "count 1 to 5"}], "user": "user", "stream": True}
t0 = time.time()
r = requests.post(BASE_URL + "v2/grammar", headers=hdr(), json=body, timeout=120, stream=True)
print("status:", r.status_code, "content-type:", r.headers.get("content-type"), f"{time.time()-t0:.2f}s")
chunks = 0
for line in r.iter_lines():
    if line:
        chunks += 1
        if chunks <= 4:
            print("  ", line[:160])
print("总行数:", chunks)

print("\n############ system 角色是否被接受 ############")
body = {"model": "gpt-5-mini", "messages": [
    {"role": "system", "content": "You are a pirate. Answer in one word."},
    {"role": "user", "content": "hello"}], "user": "user"}
r = requests.post(BASE_URL + "v2/grammar", headers=hdr(), json=body, timeout=120)
print(json.dumps(r.json(), ensure_ascii=False)[:400])
