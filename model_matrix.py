"""模型/端点矩阵探测：定位上游实际支持的模型白名单"""
import time

from probe import make_jwt, BASE_URL, UA
import requests

MODELS = [
    "gpt-3.5-turbo", "gpt-3.5-turbo-0125", "gpt-4", "gpt-4-turbo", "gpt-4o", "gpt-4o-mini",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "o3-mini", "o4-mini", "gpt-5-mini",
    "claude-3-haiku-20240307", "claude-3-5-sonnet-20241022", "gemini-1.5-flash",
    "deepseek-chat", "qwen-turbo", "llama-3.1-8b-instant", "text-davinci-003",
]

ENDPOINTS = ["grammar", "rephrase", "email_composer"]
BODY_TMPL = {
    "model": "%MODEL%",
    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    "user": "user",
}


def hdr():
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": UA,
        "Authorization": "Bearer " + make_jwt(),
    }


for ep in ENDPOINTS:
    print(f"\n############ endpoint={ep} ############")
    for m in MODELS:
        body = dict(BODY_TMPL)
        body["model"] = m
        t0 = time.time()
        try:
            r = requests.post(BASE_URL + "v2/" + ep, headers=hdr(), json=body, timeout=90)
            dt = time.time() - t0
            txt = r.text.replace("\n", " ")[:260]
            print(f"[{r.status_code}] {m:32s} {dt:5.2f}s {txt}")
        except Exception as e:
            print(f"[ERR] {m:32s} {e}")
