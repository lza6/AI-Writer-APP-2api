# new-api 对接手册 · AI Writer 渠道

> 面向在 new-api 里**手工新建渠道**的操作者。本文件只描述字段级配置与验证步骤，不改动任何 new-api 代码。
> 上游事实来源：`docs/REVERSE-REPORT.md`（全部经实际请求验证）。

## 1. 上游硬性事实（决定怎么配）

| 事实 | 影响 |
|---|---|
| 上游**无 SSE**，`stream:true` 直接 400 | new-api 渠道不能声明流式；用本仓库网关的伪流式兜底 |
| 上游只有一个真正的路由 `v2/grammar`（+ `rephrase`、`email_composer`），非 OpenAI 标准路径 | **必须**经过本仓库网关做路径/鉴权翻译，不能把 `https://grammar.metaverselabs.ai` 直接当 OpenAI Base URL 填 |
| 鉴权是自签 HS256 JWT，5 分钟过期 | 由网关每请求现签，new-api 侧 Key 随便填 |
| `temperature`/`top_p` 只收整数，小数 400 | 网关已丢弃小数（见 `server.py::build_upstream_body`） |
| 上游并发 8 即出现 nginx 503，吞吐恒定 ~0.17 rps | 渠道权重给低、超时给长、开重试 |
| 可用模型只有 `gpt-5-mini`、`gpt-5-nano` | 模型映射只配这两个 |

## 2. 形态选择

| 方案 | 做法 | 结论 |
|---|---|---|
| A（推荐） | 本机/内网跑 `2api/server.py`，new-api 渠道指向它 | ✅ 已验证 |
| B | 把 `server.py` 的翻译逻辑写成 new-api Go adaptor | 可做，但上游吞吐这么低，不值得 |
| C | new-api 渠道直接填 `https://grammar.metaverselabs.ai/api` | ❌ 必炸：路径不是 `/v1/chat/completions`，鉴权也不是静态 Bearer |

## 3. 渠道字段（方案 A）

| 字段 | 值 |
|---|---|
| 类型 | OpenAI（`type=1`） |
| Base URL | `http://<网关内网地址>:8951`（**不要**加 `/v1`，网关同时接受带/不带前缀；`/v1/chat/completions` 是标准路径） |
| 密钥 | 任意非空字符串（如 `aiwriter-local`）——网关不校验；填值才能让 new-api 保存渠道 |
| 模型 | `gpt-5-mini`、`gpt-5-nano` |
| 模型重定向/映射 | 若对外暴露自有模型名，映射到上面两个；**不要**映射到 `gpt-5`/`gpt-4o`（上游无权限） |
| 分组 | 单独建分组（如 `writer`），便于限制调用人群 |
| 权重/优先级 | 权重 1，优先级低（避免被当主力） |

网关侧建议环境变量：

```bash
AIW_PORT=8951
AIW_CONCURRENCY=3        # 上游是串行单通道，3 已足够；调高只会排队并放大 503
AIW_RETRIES=3            # nginx 503 重试
```

## 4. 计费建议（防暴扣）

- 该上游真实成本极低（Azure gpt-5-mini），但**没有官方价目**。不要用默认兜底倍率。
- 建议按次或按 token 设低倍率：先跑 20 次真实请求，用响应里的 `usage` 统计平均 token
  （实测：短问答 150~350 tokens，中文长文 1000 tokens 左右，含 reasoning tokens）。
- 必须在 `设置 → 运营设置 → 模型倍率` 里给 `gpt-5-mini` / `gpt-5-nano` **显式定价**，否则会命中兜底倍率（历史踩坑：未命中兜底 37.5）。
- 若只想做"写作工具"入口，可用固定按次计费，避免 reasoning tokens 造成账单不可预期。

## 5. 超时与重试

| 项 | 建议值 | 原因 |
|---|---|---|
| 上游超时（new-api 侧） | **≥ 120 s** | `reasoning_effort` 默认时单请求实测 5~50 s，并发排队后可达 60 s+ |
| 重试次数 | 2~3 | 503 是瞬时连接限流，重试即恢复 |
| 渠道健康检查模型 | `gpt-5-mini` + `max_completion_tokens≤16` + `reasoning_effort=minimal` | 否则健康检查本身就要十几秒 |

## 6. 验收四步（每步都要看到实际输出）

1. **网关存活**
   ```bash
   curl -s http://127.0.0.1:8951/health
   # 期望 {"ok":true,"mock":false,"models":["gpt-5-mini","gpt-5-nano"],...}
   ```
2. **直连模型列表**
   ```bash
   curl -s http://127.0.0.1:8951/v1/models
   ```
3. **经 new-api 中转真实出文本**（用渠道测试或 API 令牌调用 `/v1/chat/completions`），期望
   `choices[0].message.content` 非空、`usage.total_tokens > 0`。
4. **验证计费落账**：调用后查 `日志` 明细，确认 quota 扣减额与你设置的倍率一致，且**不是**兜底值。

## 7. 排错表

| 现象 | 原因 | 处理 |
|---|---|---|
| new-api 报 404 | Base URL 填了 `https://grammar.metaverselabs.ai/api` 直连上游 | 改成网关地址 |
| 401 `cookie token is empty` | 网关未启用/Key 指向了上游域名 | 检查 Base URL 是否指向网关 |
| 400 `cannot unmarshal number into … of type int` | 客户端传了小数 `temperature`/`top_p`，且绕过了网关字段过滤 | 走 `/v1/chat/completions`（已过滤），或客户端传整数 |
| 400 `Invalid Signature`/`code:14` | 请求了 gpt-4 家族或其它分流模型 | 只用 `gpt-5-mini` / `gpt-5-nano` |
| 502 `HTTP 503 … nginx` | 上游连接限流 | 降 `AIW_CONCURRENCY`，确认 `AIW_RETRIES≥3` |
| 流式调用报错 | 客户端 `stream:true` 直连上游 | 走网关（伪流式），或让客户端用非流式 |
| 响应文本带双引号 | App 侧会剥引号，网关原始返回保留 | 如需一致行为，客户端自行 `strip('"')` |

## 8. 交接清单

- [ ] 部署 `2api/server.py`（建议 systemd/nssm 保活，端口 8951）
- [ ] `AIW_CONCURRENCY=3`、`AIW_RETRIES=3` 已设
- [ ] new-api 新建 OpenAI 类型渠道，Base URL = 网关地址，模型 = `gpt-5-mini`/`gpt-5-nano`
- [ ] 模型倍率显式配置（禁用兜底）
- [ ] 上游超时 ≥120 s
- [ ] 验收四步全部有实际输出留档
- [ ] 已知风险告知：签名密钥公开、上游随时可能更换 secret/baseUrl，**失效后需重新解包 APK 取 `remote_config_defaults.xml`**
