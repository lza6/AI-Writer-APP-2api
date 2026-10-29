"""第三轮：参数/流式/角色/并发探测（修正响应非 JSON 情况）"""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from probe import BASE_URL, UA, make_jwt

EP = BASE_URL + "v2/grammar"


def hdr():
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": UA,
        "Authorization": "Bearer " + make_jwt(),
    }


def post(body, timeout=180, **kw):
    t0 = time.time()
    r = requests.post(EP, headers=hdr(), json=body, timeout=timeout, **kw)
    return r, time.time() - t0


def show(tag, r, dt):
    ct = r.headers.get("content-type", "")
    body = r.text
    try:
        d = json.loads(body)
        if isinstance(d, dict) and d.get("choices"):
            msg = d["choices"][0]["message"]
            print(f"[OK ] {tag} {dt:.2f}s content={str(msg.get('content'))[:70]!r} "
                  f"reasoning={'Y' if msg.get('reasoning_content') else 'N'} usage={d.get('usage')} model={d.get('model')}")
            return
        print(f"[-- ] {tag} {dt:.2f}s [{r.status_code}] {json.dumps(d)[:200]}")
    except Exception:
        print(f"[???] {tag} {dt:.2f}s [{r.status_code}] ct={ct} raw={body[:200]!r}")


print("############ 参数矩阵 ############")
for extra in [
    {"max_completion_tokens": 32},
    {"temperature": 0.2, "max_completion_tokens": 32},
    {"top_p": 0.5, "max_completion_tokens": 16},
    {"n": 2, "max_completion_tokens": 16},
    {"reasoning_effort": "minimal", "max_completion_tokens": 32},
    {"reasoning_effort": "low", "max_completion_tokens": 32},
]:
    body = {"model": "gpt-5-mini", "messages": [{"role": "user", "content": "1+1=?"}], "user": "user", **extra}
    r, dt = post(body)
    show(f"gpt-5-mini {extra}", r, dt)

print("\n############ 流式 ############")
body = {"model": "gpt-5-mini", "messages": [{"role": "user", "content": "count 1 to 5"}], "user": "user", "stream": True}
t0 = time.time()
r = requests.post(EP, headers=hdr(), json=body, timeout=180, stream=True)
print("status:", r.status_code, "ct:", r.headers.get("content-type"), f"{time.time()-t0:.2f}s")
n, first = 0, None
for line in r.iter_lines():
    if line:
        n += 1
        if n <= 3:
            print("  ", line[:180])
print("行数:", n)

print("\n############ 多轮/system ############")
cases = {
    "system+user": [{"role": "system", "content": "You are a pirate. Answer in one word."}, {"role": "user", "content": "hello"}],
    "assistant 历史": [{"role": "user", "content": "My name is Bob."}, {"role": "assistant", "content": "Hi Bob!"}, {"role": "user", "content": "What is my name?"}],
    "空 messages": [],
}
for tag, msgs in cases.items():
    body = {"model": "gpt-5-mini", "messages": msgs, "user": "user"}
    r, dt = post(body)
    show(tag, r, dt)

print("\n############ 并发 8 ############")


def one(i):
    body = {"model": "gpt-5-mini", "messages": [{"role": "user", "content": f"say number {i}"}],
            "user": "user", "max_completion_tokens": 64}
    try:
        r, dt = post(body, timeout=180)
        d = r.json()
        return i, r.status_code, dt, bool(d.get("choices")), str(d.get("error"))[:80]
    except Exception as e:
        return i, 0, 0.0, False, str(e)[:80]


with ThreadPoolExecutor(max_workers=8) as ex:
    res = list(ex.map(one, range(8)))
for r_ in res:
    print("  ", r_)
lat = sorted(x[2] for x in res if x[3])
print("成功:", sum(1 for x in res if x[3]), "/8", "p50:", lat[len(lat) // 2] if lat else None, "max:", lat[-1] if lat else None)
