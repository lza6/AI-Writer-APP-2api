# AI Writer v1.0.10 逆向报告

- 目标：`AI Writer v1.0.10.apk`（46.9 MB，包名 `com.smartwidgetlabs.ai.writer`，versionName 1.0.10 / versionCode 53）
- 发行方：SmartWidgetLabs；签名 `CN=TRUMods`（`SHA1 99:74:B5:20:18:60:AC:AC:8A:14:F3:15:47:CF:D3:1A:13:27:D5:B0`，Mod 版重签）
- 技术栈：Kotlin + Jetpack Compose、Koin DI、Retrofit2 + OkHttp、Room（本地历史）、Firebase（Analytics/Crashlytics/Messaging/RemoteConfig）、AppLovin/AdMob/Pangle 广告、vulcanlabs 内部库
- 加固：**无壳**。7 个 dex 全明文，`libaiwriter.so` 仅有模板 `Java_..._stringFromJNI`，无业务 native 逻辑
- 分析方式：纯静态（jadx + apktool 解资源），未使用模拟器 / Frida

---

## 1. 鉴权链路（核心突破）

```
MainApplication.baseUrl()  ← Remote Config "jwtParam".baseUrl
K40.a.v()  (default-config 表, key = "jwtParam")
   └─ C4240tI.a(json)  Gson → JwtParam(secret, token, timeExp, baseUrl)
        └─ JWebTokenKt.getToken() → JWebToken(secret, token, now+timeExp*60s)
             └─ header {"alg":"HS256","typ":"JWT"} + payload {"token":…,"exp":…}
                Base64.NO_WRAP|NO_PADDING|URL_SAFE + HMAC-SHA256
                   └─ BaseServiceFactoryKt.headerInterceptor
                        Authorization: Bearer <jwt>
                        User-Agent: com.smartwidgetlabs.ai.writer 1.0.10 53 Android SDK: <sdk> (<release>)
```

`res/xml/remote_config_defaults.xml` 中的原始条目（**这是全部凭证，无任何加密**）：

```json
{"secret":"<REDACTED>","token":"admin","timeExp":5,"baseUrl":"https://grammar.metaverselabs.ai/api/"}
```

要点：
- `secret` 是**共享静态值**，不是每设备派生 → 任何人解包即可复现签名。
- `token` 字段值服务端**不校验具体值**：实测把 `admin` 换成 `some-random-user-42` 依旧 200。
- 服务端校验是严格的（gin-jwt 默认行为）：错误 secret / `exp` 过期 / 缺 `Authorization` 全部 401
  （缺 header 时返回 `{"code":401,"message":"cookie token is empty"}`，`Www-Authenticate: JWT realm=Flight Tracker API`）。
- 结论：**无用户体系、无设备绑定、无证书固定**——一份 secret 对应全站共用的一套上游额度。

## 2. 端点表

Retrofit 接口：`AiWriterRemoteService`

```java
@POST("v2/{endPoint}")  completionsWithPathV2(@Path(encoded=true) String endPoint, @Body CompletionMessageParam)
```

`endPoint` = `EndpointConfigKt.endpoint(config)`，即 Remote Config `endpointConfig[].endpointName`：

| App 功能 (name) | 上游路由 | 服务端 prompt 注入 |
|---|---|---|
| writing | `v2/grammar` | 无（纯透传） |
| expand | `v2/grammar` | 无 |
| shorten | `v2/grammar` | 无 |
| rephrase | `v2/rephrase` | 无 |
| email_composer | `v2/email_composer` | 无 |

路由白名单实测（`已验证`）：

| 请求 | 结果 |
|---|---|
| `POST /api/v2/grammar` | 200 → Azure OpenAI `/v1/chat/completions` |
| `POST /api/v2/rephrase` | 200 → 同上 |
| `POST /api/v2/email_composer` | 200 → 同上 |
| `POST /api/v2/writing` `/expand` `/shorten` | **404**（App 从不直接打，均走 grammar） |
| `POST /api/v1/grammar` | 200，但转发 `/v1/completions`（chat 模型报 "not supported in the v1/completions endpoint"） |
| `GET /api/v2/models` `/api/models` `/api/health` `/api/v2/chat/completions` 等 | 全部 404 |

## 3. 请求 / 响应契约

请求（App 侧 DTO `CompletionMessageParam`，字段仅 3 个）：

```json
{"model":"gpt-3.5-turbo","messages":[{"role":"system","content":"…"},{"role":"user","content":"…"}],"user":"user"}
```

- App 的 role 用法：`writing`/`email_composer` 只发一条 `user`；`rephrase`/`expand`/`shorten` 发 `system`(prompt)+`user`(正文)
- 响应：标准 OpenAI `chat.completion`（`choices[0].message.content`），客户端再剥掉首尾双引号
- `user` 固定 `"user"`，与限额统计无关（服务端无配额）

服务端实际接受但 App 未使用的字段（实测透传成功，`已验证`）：

| 字段 | 行为 |
|---|---|
| `max_completion_tokens` | 生效 |
| `max_tokens` | 透传到 Azure，被拒："not supported with this model. Use max_completion_tokens" |
| `reasoning_effort` | 生效（minimal→10 tokens，low→74，default→74~996） |
| `n` | 生效（usage 翻倍），但网关只回 1 个 choice |
| `temperature` / `top_p` | **只接受整数**：小数直接 400 `json: cannot unmarshal number 0.2 into Go struct field request.temperature of type int` |
| `stream` | **不支持**：400 `invalid character 'd' looking for beginning of value` |
| system / assistant 多轮 | 正常支持（连续对话已验证） |

## 4. 上游身份

- 域名 `grammar.metaverselabs.ai`（nginx/1.14.0 Ubuntu），CORS 全开
- 网关为 Go 服务（gin + gin-jwt），把 3 个路由映射到 Azure OpenAI
- 模型错误暴露 Azure 项目 ID：`Project proj_X1WDGCYLzyHLtbhST4XJNkkN does not have access to model 'gpt-5'`
- 模型可用矩阵（`已验证`，全端点一致）：

| 模型 | 结果 |
|---|---|
| `gpt-5-mini` | ✅ 真实出文本（`model: gpt-5-mini-2025-08-07`） |
| `gpt-5-nano` | ✅ 真实出文本（`gpt-5-nano-2025-08-07`） |
| `gpt-5` / `gpt-5-pro` / `gpt-5.1` / `gpt-5.2` / `o3` | ❌ Azure 项目无访问权限 |
| `gpt-4` / `gpt-4o` / `gpt-4.1*` / `chatgpt-4o-latest` | ❌ `{"data":null,"error":{"message":"Invalid Signature","code":14}}` |
| `gpt-3.5-turbo` / `o1` / `o3-mini` / `o4-mini` / `text-davinci-003` | ❌ OpenAI 侧已下线 |
| claude / gemini / deepseek / qwen / llama | ❌ 不存在或无权限 |

> `gpt-4` 家族的 `Invalid Signature code:14` 来自**另一条上游分支**（响应结构为 `{"data":null,"error":…}` 而非 OpenAI 结构），
> 说明网关按模型分流到第二个供应商。该分支的签名方案未还原（`待验证`，需要抓 App 侧真实流量才能确定是否有额外 header）。

## 5. 限流与吞吐（真实压测）

上游 nginx 层连接限制，超限返回 **`503 Service Temporarily Unavailable`（HTML）**，不是 429：

| 并发 | 请求数 | 成功 | p50 | p90 | max | 吞吐 |
|---|---|---|---|---|---|---|
| 2 | 8 | 8/8 (100%) | 11.71 s | 13.12 s | 17.68 s | 0.18 rps |
| 4 | 8 | 8/8 (100%) | 23.84 s | 25.40 s | 25.56 s | 0.16 rps |
| 8 | 8 | 8/8 (100%) | 33.49 s | 41.48 s | 49.21 s | 0.16 rps |
| 16（裸打，不重试） | 16 | 10/16 | — | — | — | 6×503 |
| 32（MOCK 网关自身） | 32 | 32/32 | 0.01 s | 0.01 s | 0.01 s | **1157 rps** |

推理：
- p50 随并发线性增长、rps 恒定 → 上游是**串行单通道**（Azure 部署低 TPM），并发只是排队。
- 网关侧（我们的 server.py）加 6 并发闸门 + 503 重试后，8 并发仍 100% 成功，但延迟被排队拉长。
- 生产建议：**并发 2~3 封顶**，把慢请求超时设到 60~120 s，不要指望用它顶流量。

## 6. 客户端"额度"是纯本地计数

- Remote Config：`dailyFreeUsageConfig={"dailyFreeUsageNumber":5,"isDailyReset":true}`、`daily_rewarded_ads_config={"dailyReward":100000,"freeUsage":2}`
- 计数存 SharedPreferences（`STRING_JSON_DAILY_FREE_USAGE` / `STRING_JSON_DAILY_REWARD_ADS`），**服务端不做任何配额校验**
- 实测累计 100+ 次真实调用从未出现配额/额度类错误 → 所谓"每日 5 次免费"只是 UI 门槛
- 订阅项：`aiwriter.monthly / yearly / weekly / weeklytrial`，走 Google Play Billing，与上游鉴权无关

## 7. 其他后端面

| 能力 | 实现 | 是否可复用 |
|---|---|---|
| 写作/改写/邮件/扩写/缩写 | `grammar.metaverselabs.ai` | ✅ 本仓库已封装 |
| TTS | Google Cloud Text-to-Speech 直连（`texttospeech.googleapis.com`），`isSpeakUsingGCP` 默认 false | ⚠️ 需要独立 Google 付费 key，未纳入 |
| STT | Android 系统 SpeechRecognizer | — |
| 历史记录 | 本地 Room（`AiWriterDatabase`），不上传 | — |
| 配置下发 | Firebase Remote Config（项目 `ai-writer-android-f3094`） | ✅ 全部默认值已从 APK 静态取得 |
| 埋点/广告 | Firebase Analytics、AppLovin、AdMob、Pangle | 无关 |

## 8. 实战坑（复现时按序看）

1. **不要找 `/api/` 路径字符串**：dex 里只有裸 `https://grammar.metaverselabs.ai/api/`，路径由 Retrofit `v2/{endPoint}` 拼装，endPoint 又来自远端配置 → 必须先把 Remote Config 默认值解出来。
2. **`res/xml/remote_config_defaults.xml` 是金矿**：Firebase Remote Config 的 App 内置默认值会**明文**打进 APK，安全关键参数（secret/baseUrl/prompt/广告位）常在此。
3. **`endpointName` ≠ `name`**：`writing` 请求实际打 `v2/grammar`，直接打 `v2/writing` 是 404。
4. **JWT 是自签不是服务端下发**：App 用共享 secret 自己 `HmacSHA256` 签，payload 只有 `token`/`exp`，所以本地可完全离线复现。
5. **base64 是 URL_SAFE + NO_PADDING + NO_WRAP**（Android flag 11），用标准库默认 padding 会 401。
6. **`temperature`/`top_p` 上游 Go struct 是 int**：小数透传直接 400；对接层要主动丢弃或取整。
7. **上游不支持 SSE**：`stream:true` 让 Go 网关解析失败（把 "data: …" 当 JSON 解）→ 必须网关侧伪流式。
8. **限流表现是 nginx 503 而非 429**：捕获非 JSON 的 HTML 503 并重试，否则误判成"协议坏了"。
9. **`Www-Authenticate: JWT realm=Flight Tracker API`**：gin-jwt 库默认 realm 名，能快速判断服务端框架与 401 语义。
10. **模型报错信息会泄露上游真身**：`Project proj_XXX does not have access to model` 直接确认 Azure OpenAI + 项目 ID。
11. **别用 curl 在 GBK 终端直接传中文 JSON**：本次 `UnicodeDecodeError` 是终端编码造成，不是服务端问题——用文件 `--data-binary @file` 或 Python 客户端。

## 9. 风险提示（给上游方）

- 签名密钥硬编码于 Remote Config 默认值 → 任何人解包 APK 即可无限制调用其 Azure 端点；
- 服务端**无 RPM/TPM 之外的配额**，也没有设备/用户维度限流；
- `token` 字段可任意填 → 无法做用户级审计或封禁。

修复方向：改为服务端下发短期 token（服务端持有签名密钥）、按设备/用户维度配额、启用证书固定 + 至少一层请求签名。
