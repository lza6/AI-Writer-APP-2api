"""AI Writer v1.0.10 上游协议验证脚本 — 静态提取的 JWT 自签凭证直连测试"""
import base64
import os
import hashlib
import hmac
import json
import sys
import time

import requests

BASE_URL = "https://grammar.metaverselabs.ai/api/"
SECRET = os.environ.get("AIW_JWT_SECRET", "")  # 从 APK res/xml/remote_config_defaults.xml 的 jwtParam.secret 取
TOKEN = "admin"
UA = "com.smartwidgetlabs.ai.writer 1.0.10 53 Android SDK: 33 (13)"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_jwt(secret: str = SECRET, token: str = TOKEN, ttl: int = 300) -> str:
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps({"token": token, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": UA,
        "Authorization": "Bearer " + make_jwt(),
    }


def probe(endpoint: str, body: dict, timeout: int = 60):
    url = BASE_URL + "v2/" + endpoint
    t0 = time.time()
    r = requests.post(url, headers=headers(), json=body, timeout=timeout)
    dt = time.time() - t0
    print(f"\n=== POST {url} -> {r.status_code} in {dt:.2f}s ===")
    print(r.text[:2000])
    return r


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "writing"
    if mode == "writing":
        probe("writing", {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Write an essay exploring AI agents. Length: Short, using technique: None."}],
            "user": "user",
        })
    elif mode == "rephrase":
        probe("rephrase", {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": 'Rephrase the user message using "tone_input" tone, return only the revised paragraph in a double quote.'},
                {"role": "user", "content": "The quick brown fox jumps over the lazy dog."},
            ],
            "user": "user",
        })
    elif mode == "raw":
        endpoint = sys.argv[2]
        probe(endpoint, json.loads(sys.argv[3]))
