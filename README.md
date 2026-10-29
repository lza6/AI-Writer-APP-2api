# AI Writer v1.0.10 → OpenAI 兼容 2API

`com.smartwidgetlabs.ai.writer` 的上游协议静态还原 + 本地 OpenAI 兼容网关 + 压测 + 演示页。
**零 Frida、零模拟器**：全部凭证从 APK 内 Firebase Remote Config 默认值中静态取得。

## 一句话结论

App 的鉴权是**客户端自签的 HS256 JWT**（secret 硬编码在 Remote Config 默认值里），上游为一台 Go 网关后接
**Azure OpenAI**。签名密钥、固定身份 `token=admin`、baseUrl 三者全部位于
`res/xml/remote_config_defaults.xml` 的 `jwtParam` 条目中，解包即得。

## 快速开始

### 0. 取密钥（仓库内不含任何密钥，必须自己从 APK 提取）

```bash
# 反编译资源（注意是 apktool，纯 unzip 拿不到该 XML）
java -jar apktool.jar d --no-src -f -o out "AI Writer v1.0.10.apk"
grep -A1 '<key>jwtParam</key>' out/res/xml/remote_config_defaults.xml
# 形如: {"secret":"<REDACTED>","token":"admin","timeExp":5,"baseUrl":"https://……/api/"}

export AIW_JWT_SECRET='<上面 secret 的值>'      # 必备，否则服务拒绝启动
export AIW_BASE_URL='<上面 baseUrl 的值>'        # 可选，默认内置同一域名
```

同一个 XML 里的 `endpointConfig` 还给出 5 个功能的 prompt 模板和 `endpointName`（真实路由段），
`dailyFreeUsageConfig` 给出客户端的每日免费次数 —— 即 App 行为的全部权威定义。

### 1. 起服务

```bash
cd 2api
pip install fastapi uvicorn httpx requests          # 运行依赖
AIW_PORT=8951 python server.py                       # 默认 8940；本机 8940 已被其它 2API 占用，故用 8951
# 演示页由服务在 http://127.0.0.1:8951/ 直接提供（demo.html）

# 真实调用
curl -s http://127.0.0.1:8951/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model":"gpt-5-mini","messages":[{"role":"user","content":"用一句话介绍你自己"}],
  "reasoning_effort":"minimal","max_completion_tokens":300}'

# 原生五功能（按 App 内置 prompt 模板）
curl -s http://127.0.0.1:8951/v1/aiwriter/rephrase -H 'Content-Type: application/json' \
  -d '{"text":"The cat sat on the mat.","tone":"Casual"}'

# 压测 / 端到端
python bench.py --levels 2,3 --requests 8 --json bench-real.json
python verify_demo.py
```

MOCK 模式（不访问上游，验证接入链路）：`AIW_MOCK=1 AIW_PORT=8952 python server.py`

## 文件

| 文件 | 作用 |
|------|------|
| `2api/server.py` | OpenAI 兼容网关（`/v1/models`、`/v1/chat/completions` 含伪流式、`/v1/aiwriter/*` 原生模式）、503 重试、并发闸门、MOCK |
| `2api/demo.html` | 单页控制台：模式/功能/模型/effort 切换、耗时与 token 计量、原始 JSON 视图 |
| `2api/bench.py` | 并发阶梯压测（延迟分位、成功率、错误分布） |
| `2api/verify_demo.py` | Playwright 端到端（真实 + MOCK 双跑） |
| `probe.py` / `probe2.py` / `probe3.py` / `model_matrix.py` | 协议还原期的探测脚本（鉴权、模型矩阵、参数透传、流式、并发；保留作证据） |
| `docs/REVERSE-REPORT.md` | 逆向报告：证据链、端点表、DTO、11 条实战坑 |
| `docs/NEW-API-INTEGRATION.md` | 接入 new-api 的字段级手册 |

## 实测摘要（2026-10-29）

| 项目 | 结果 |
|------|------|
| 鉴权 | 自签 JWT 通过；错误 secret / 过期 exp / 无 header 均 401（`已验证`） |
| 可用模型 | `gpt-5-mini`、`gpt-5-nano`（`已验证`，各端点均真实出文本） |
| 不可用 | `gpt-5`/`o3`/`gpt-5.1` = Azure 项目无权限；`gpt-4`/`gpt-4o` 家族 = `Invalid Signature code:14` |
| 流式 | 上游**不支持** SSE（`stream:true` → 400），网关侧提供伪流式 |
| 真实压测 | 2/4/8 并发各 8 请求 **24/24 成功**，p50 分别 11.7 / 23.8 / 33.5 s，吞吐恒 ~0.17 rps |
| 上游限流 | nginx 层 503（连接数限制），8 并发即触发，重试可恢复 |
| 网关自身 | MOCK 模式 32 并发 **1157 rps**，网关无瓶颈 |
| 端到端 | Playwright 4/4 PASS（MOCK chat/native + 真实 chat/native） |

> **吞吐结论**：上游是低频单通道（≈10 请求/分钟），适合个人写作助手，不适合当通用高并发渠道。
