"""
AI Writer v1.0.10 → OpenAI 兼容 2API 网关

上游协议（静态还原，零动态）：
  POST https://grammar.metaverselabs.ai/api/v2/{grammar|rephrase|email_composer}
  Authorization: Bearer <HS256 JWT，secret=AIW_JWT_SECRET（从 APK 的 remote_config_defaults.xml 取），
                     payload {"token":"admin","exp":now+300}>

特性：
  - /v1/models、/v1/chat/completions（OpenAI 兼容，含伪流式）
  - 原生模式：/v1/aiwriter/{writing|rephrase|email|expand|shorten}，按 App 内置 prompt 模板填充
  - 503 重试（nginx 连接限流）+ 并发闸门 + 429/5xx 统一错误映射
  - MOCK=1 时全链路假响应，用于无上游额度时验证接入
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import uuid

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

# ---------------------------------------------------------------- 上游常量
BASE_URL = os.environ.get("AIW_BASE_URL", "https://grammar.metaverselabs.ai/api/")
JWT_SECRET = os.environ.get("AIW_JWT_SECRET", "")  # 必填：从 APK 的 res/xml/remote_config_defaults.xml 提取（见 README）
JWT_TOKEN = os.environ.get("AIW_JWT_TOKEN", "admin")
JWT_TTL = int(os.environ.get("AIW_JWT_TTL", "300"))
UPSTREAM_UA = os.environ.get("AIW_USER_AGENT", "com.smartwidgetlabs.ai.writer 1.0.10 53 Android SDK: 33 (13)")

# 上游真实可用的路由（其余一律 404；writing/expand/shorten 都映射到 grammar）
ENDPOINTS = ("grammar", "rephrase", "email_composer")
ENDPOINT_BY_FEATURE = {
    "writing": "grammar",
    "expand": "grammar",
    "shorten": "grammar",
    "rephrase": "rephrase",
    "email": "email_composer",
    "email_composer": "email_composer",
}

AVAILABLE_MODELS = ["gpt-5-mini", "gpt-5-nano"]
UNKNOWN_MODEL_HINT = "gpt-5-mini"

# App 内置 prompt 模板（Remote Config endpointConfig 原文）
FEATURE_PROMPTS = {
    "writing": 'Write an essay exploring %topic%. Length: %length%, using technique: %technique%, length: %length%.',
    "rephrase": 'Rephrase the user message using "%tone%" tone, return only the revised paragraph in a double quote.',
    "email": 'Compose an email to "%receiver%" using "%tone%" tone with the content "%content%", '
             "return include both subject and body of the email",
    "expand": "Make the user's message longer, add more details. Use \"%tone%\" writing tone. "
              "Your words are never longer than %level% words.",
    "shorten": "Make the user's message shorter. Use \"%tone%\" writing tone. "
               "Your words are never longer than %level% words.",
}

MAX_CONCURRENCY = int(os.environ.get("AIW_CONCURRENCY", "6"))
RETRIES = int(os.environ.get("AIW_RETRIES", "3"))
MOCK = os.environ.get("AIW_MOCK", "0") == "1"
PORT = int(os.environ.get("AIW_PORT", "8940"))

app = FastAPI(title="AI Writer 2API", docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_sem = asyncio.Semaphore(MAX_CONCURRENCY)
_client: httpx.AsyncClient | None = None
_stats = {"requests": 0, "upstream_ok": 0, "upstream_fail": 0, "retries": 0, "stream": 0, "started": time.time()}


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_jwt(secret: str = JWT_SECRET, token: str = JWT_TOKEN, ttl: int = JWT_TTL) -> str:
    """与 App 的 JWebToken 完全一致：header/payload 紧凑 JSON + base64url 无填充 + HMAC-SHA256。"""
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(json.dumps({"token": token, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def upstream_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": UPSTREAM_UA,
        "Authorization": "Bearer " + make_jwt(),
    }


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0), limits=httpx.Limits(max_connections=32))
    return _client


async def call_upstream(endpoint: str, body: dict) -> dict:
    """调用上游；503（nginx 连接限流）与网络错误按指数退避重试。"""
    url = BASE_URL + "v2/" + endpoint
    last_err: str = ""
    for attempt in range(1, RETRIES + 1):
        async with _sem:
            try:
                r = await get_client().post(url, headers=upstream_headers(), json=body)
            except httpx.HTTPError as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            else:
                if r.status_code == 200:
                    try:
                        data = r.json()
                        _stats["upstream_ok"] += 1
                        return {"ok": True, "data": data}
                    except ValueError:
                        last_err = f"non-json 200: {r.text[:200]!r}"
                else:
                    last_err = f"HTTP {r.status_code}: {r.text[:200]!r}"
                    if r.status_code not in (429, 500, 502, 503, 504):
                        _stats["upstream_fail"] += 1
                        return {"ok": False, "status": r.status_code, "error": last_err}
        if attempt < RETRIES:
            _stats["retries"] += 1
            await asyncio.sleep(1.2 * attempt)
    _stats["upstream_fail"] += 1
    return {"ok": False, "status": 503, "error": last_err}


def openai_error(message: str, code: str = "upstream_error", status: int = 502) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": "upstream_error", "param": None, "code": code}},
    )


def normalize_messages(payload: dict) -> list[dict]:
    raw = payload.get("messages")
    if not isinstance(raw, list) or not raw:
        raise ValueError("messages is required and must be a non-empty array")
    out = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):  # 多段 content 拼成纯文本
            content = "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
        if role in ("system", "user", "assistant") and content:
            out.append({"role": role, "content": str(content)})
    if not out:
        raise ValueError("no usable message with role system/user/assistant")
    return out


def build_upstream_body(payload: dict, endpoint: str | None = None) -> tuple[dict, str]:
    model = str(payload.get("model") or UNKNOWN_MODEL_HINT)
    if "/" in model:  # 允许 new-api 侧用 aiwriter/xxx 前缀区分渠道
        model = model.split("/", 1)[1]
    body = {"model": model, "messages": normalize_messages(payload), "user": str(payload.get("user") or "user")}
    # 可用模型全是 gpt-5 家族：上游对 max_tokens 直接报错，统一翻译成 max_completion_tokens
    completion_cap = payload.get("max_completion_tokens")
    if completion_cap is None and payload.get("max_tokens") is not None:
        completion_cap = payload["max_tokens"]
    if completion_cap is not None:
        body["max_completion_tokens"] = completion_cap
    # 上游 Go 网关只接受这些字段；temperature/top_p 是 int 类型，透传小数会被 400
    for key in ("n", "reasoning_effort", "stop", "seed"):
        if key in payload and payload[key] is not None:
            body[key] = payload[key]
    for key in ("temperature", "top_p"):
        val = payload.get(key)
        if isinstance(val, int):
            body[key] = val
    return body, (endpoint or "grammar")


def to_openai_response(data: dict, model: str) -> dict:
    """上游响应已是 OpenAI 结构，补齐空字段并统一 id/created。"""
    if data.get("error"):
        return data
    data["id"] = data.get("id") or "chatcmpl-" + uuid.uuid4().hex[:24]
    data["object"] = data.get("object") or "chat.completion"
    data["created"] = data.get("created") or int(time.time())
    data["model"] = data.get("model") or model
    return data


def mock_response(model: str, messages: list[dict]) -> dict:
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    text = (
        f"[MOCK] 已收到 {len(messages)} 条消息，最后一条用户输入 {len(last_user)} 字符。\n"
        f"这是 AI Writer 2API 的本地假响应，用于验证链路（未访问上游）。\n"
        f"输入摘要：{last_user[:80]}"
    )
    return {
        "id": "chatcmpl-mock" + uuid.uuid4().hex[:16],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}, "index": 0, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": len(last_user) // 4, "completion_tokens": len(text) // 4,
                  "total_tokens": (len(last_user) + len(text)) // 4},
    }


def sse_chunks(text: str, model: str, chunk_size: int = 60) -> list[str]:
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    base = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model}
    frames = [dict(base, choices=[{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}])]
    for i in range(0, len(text), chunk_size):
        frames.append(dict(base, choices=[{"index": 0, "delta": {"content": text[i:i + chunk_size]}, "finish_reason": None}]))
    frames.append(dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]))
    return [f"data: {json.dumps(f, ensure_ascii=False)}\n\n" for f in frames] + ["data: [DONE]\n\n"]


def extract_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return (msg.get("content") or choices[0].get("text") or "").strip()


def render_feature(feature: str, payload: dict) -> dict:
    """把 App 的 UI 参数套进官方 prompt 模板，生成等价的 chat 请求体（原生模式）。"""
    ep = ENDPOINT_BY_FEATURE.get(feature)
    if ep is None:
        raise ValueError(f"unknown feature: {feature}")
    tpl = FEATURE_PROMPTS[feature]
    user_text = str(payload.get("text") or "").strip()
    values = {
        "%topic%": str(payload.get("topic") or user_text),
        "%length%": str(payload.get("length") or "Medium"),
        "%technique%": str(payload.get("technique") or "None"),
        "%tone%": str(payload.get("tone") or "Neutral"),
        "%level%": str(payload.get("level") or 500),
        "%receiver%": str(payload.get("receiver") or "the recipient"),
        "%content%": str(payload.get("content") or user_text),
    }
    prompt = tpl
    for k, v in values.items():
        prompt = prompt.replace(k, v)
    # App 行为：writing/email 走单条 user 消息；rephrase/expand/shorten 走 system+user
    if feature in ("writing", "email"):
        messages = [{"role": "user", "content": prompt}]
    else:
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": user_text}]
    return {"endpoint": ep, "body": {"messages": messages}}


@app.get("/health")
async def health():
    return {"ok": True, "mock": MOCK, "concurrency": MAX_CONCURRENCY, "models": AVAILABLE_MODELS,
            "upstream": BASE_URL, "stats": _stats}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [
        {"id": m, "object": "model", "created": int(_stats["started"]), "owned_by": "aiwriter"} for m in AVAILABLE_MODELS
    ]}


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(os.path.dirname(__file__), "demo.html"), encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload = await request.json()
    _stats["requests"] += 1
    try:
        body, endpoint = build_upstream_body(payload)
    except ValueError as exc:
        return openai_error(str(exc), "invalid_request", 400)
    model = body["model"]
    stream = bool(payload.get("stream"))

    if MOCK:
        data = mock_response(model, body["messages"])
        if stream:
            _stats["stream"] += 1
            return StreamingResponse((c for c in sse_chunks(extract_text(data), model)), media_type="text/event-stream")
        return JSONResponse(data)

    res = await call_upstream(endpoint, body)
    if not res["ok"]:
        return openai_error(res.get("error", "upstream failed"), "upstream_error", 502)
    data = res["data"]
    if data.get("error"):
        err = data["error"]
        return openai_error(str(err.get("message")), str(err.get("code") or "upstream_error"), 400)
    data = to_openai_response(data, model)
    if stream:
        _stats["stream"] += 1
        return StreamingResponse((c for c in sse_chunks(extract_text(data), model)), media_type="text/event-stream")
    return JSONResponse(data)


@app.post("/v1/aiwriter/{feature}")
async def native_feature(feature: str, request: Request):
    """原生模式：按 App 五功能的 prompt 模板调用，保持与 App 行为一致。"""
    payload = await request.json()
    _stats["requests"] += 1
    try:
        rendered = render_feature(feature, payload)
    except ValueError as exc:
        return openai_error(str(exc), "invalid_request", 400)
    model = str(payload.get("model") or UNKNOWN_MODEL_HINT)
    body = {
        "model": model,
        "messages": rendered["body"]["messages"],
        "user": str(payload.get("user") or "user"),
        "max_completion_tokens": payload.get("max_completion_tokens") or 2000,
        "reasoning_effort": payload.get("reasoning_effort") or "minimal",
    }
    if MOCK:
        data = mock_response(model, body["messages"])
    else:
        res = await call_upstream(rendered["endpoint"], body)
        if not res["ok"]:
            return openai_error(res.get("error", "upstream failed"), "upstream_error", 502)
        data = res["data"]
        if data.get("error"):
            err = data["error"]
            return openai_error(str(err.get("message")), str(err.get("code") or "upstream_error"), 400)
        data = to_openai_response(data, model)
    return JSONResponse({"feature": feature, "upstream_endpoint": rendered["endpoint"],
                         "content": extract_text(data), "usage": data.get("usage"),
                         "raw": data})


if __name__ == "__main__":
    if not JWT_SECRET and not MOCK:
        raise SystemExit(
            "AIW_JWT_SECRET is not set.\n"
            "从目标 APK 提取: apktool d --no-src app.apk -> res/xml/remote_config_defaults.xml -> \"jwtParam\".secret\n"
            "然后 export AIW_JWT_SECRET=... (或用 AIW_MOCK=1 只跑网关自身)"
        )
    print(f"[aiwriter-2api] port={PORT} mock={MOCK} concurrency={MAX_CONCURRENCY} upstream={BASE_URL}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
