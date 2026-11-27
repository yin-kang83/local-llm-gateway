# local-llm-gateway · 本地大模型接入层

> 一句话：**把"云端大模型 API"换成"本地 Ollama"，协议不变、零依赖、失败即停、每次调用可审计。**

[English summary](#english-summary) · [MIT](LICENSE)

---

## 为什么做这个

确定性架构里，大模型只负责**读**（把自然语言抽成结构化参数），不负责**算**（金额、勾稽、判断全部交给确定性算子）。
但"读"这一步如果走云端 API，会同时丢掉四样东西：数据边界、成本可预期、离线可用、逐次可审计。

本仓库就是这一层的**最小可审计替换件**：对上层暴露 OpenAI 兼容的 `chat/completions` 契约，
对下可接 Ollama / vLLM / llama.cpp server / LM Studio / 任何 OpenAI 兼容端点。

| 维度 | 云端 API 直连 | 本接入层（本地模型） |
|---|---|---|
| 数据边界 | 题干/底稿出厂 | 不出内网，`127.0.0.1` |
| 边际成本 | 按 token 计费 | 一次部署，反复调用 |
| 离线可用 | 断网即停 | 断网照跑 |
| 失败行为 | SDK 默认重试/兜底，容易"带回一个编造的答案" | **fail-closed**：拿不到就报错，绝不猜 |
| 审计 | 平台日志，自己拿不到完整证据 | 本地 JSONL：prompt/response 哈希 + 延迟 + 尝试次数 |

## 它是什么 / 不是什么

**是**：零第三方依赖的 OpenAI 兼容客户端 + 严格 JSON 契约 + 仅哈希的证据日志 + 本机性能测量工具。

**不是**：

- 不是训练/微调框架（训模型是另一条线，本仓库不碰权重）；
- 不是 Prompt 工程平台（Prompt 属于调用方，本仓库只保证"发出去、收回来、验得住"）；
- 不是业务规则引擎（Pydantic 校验、Decimal 算子、看门狗断言在下游确定性层）。

## 快速开始

### 1. 起本地模型（Windows / C 盘示例）

```powershell
# 便携版：解压即用，不写注册表、不装服务
# 模型默认落在 %USERPROFILE%\.ollama\models（C 盘）；可用 OLLAMA_MODELS 改
ollama pull qwen2.5:1.5b
ollama list
```

> 本机实测配置与数据见 [`docs/LOCAL-SETUP.md`](docs/LOCAL-SETUP.md)（i5-8250U / 7.9 GB RAM / MX150 2 GB，CPU 推理）。
> 显存 < 3 GB 时建议直接 CPU 推理，别让模型部分卸载到 GPU（会反复换页，反而更慢）。

### 2. 安装本包（两种方式任选）

```powershell
# 方式 A：可编辑安装
pip install -e .

# 方式 B：不安装，直接加路径
$env:PYTHONPATH = "<repo>\src"
```

### 3. 自检 → 单次调用 → 测速

```powershell
# 自检：后端可达性 + 配置的模型是否真的存在（缺失时退出码 4）
llm-gateway check --base-url http://127.0.0.1:11434/v1 --model qwen2.5:1.5b

# 要求严格 JSON：不合契约退出码 5，绝不返回"半截 JSON"
llm-gateway run --prompt "只输出一行 JSON：{\"amount\": \"100.00\"}" --expect-json

# 本机测速（延迟分布 + tokens/s + 硬件信息）
llm-gateway bench --n 5
```

## 契约

### 环境变量（推荐，密钥不落 shell 历史）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_GATEWAY_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI 兼容根路径；只填 host 时自动补 `/v1` |
| `LLM_GATEWAY_MODEL` | `qwen2.5:1.5b` | 后端模型 id |
| `LLM_GATEWAY_API_KEY` | 空 | 云端兼容后端才需要；**不打印、不落盘、不写证据** |
| `LLM_GATEWAY_TIMEOUT_S` | `60` | 单请求超时（秒） |
| `LLM_GATEWAY_MAX_RETRIES` | `2` | 仅对超时 / 408 / 429 / 5xx 生效 |
| `LLM_GATEWAY_TEMPERATURE` | `0.0` | 抽参默认确定性 |
| `LLM_GATEWAY_SEED` | 空 | 可选，透传给后端 |
| `LLM_GATEWAY_MAX_PROMPT_BYTES` | `32768` | 客户端侧预算，超限不发请求 |
| `LLM_GATEWAY_MAX_RESPONSE_BYTES` | `65536` | 响应上限，超限即失败 |
| `LLM_GATEWAY_EVIDENCE_PATH` | 空 | 证据 JSONL 路径；留空则不写 |
| `LLM_GATEWAY_EVIDENCE_STORE_TEXT` | `0` | `1` 时才把响应原文写进证据（默认只存哈希） |

### 错误码（稳定，可分支）

| `code` | 退出码 | 含义 | 调用方应做 |
|---|---|---|---|
| `config_error` | 2 | 配置非法（URL/预算/参数） | 修配置，不要重试 |
| `prompt_too_large` | 2 | 题干超出预算 | 截断/拆分，不放宽闸门 |
| `backend_unreachable` | 3 | 连不上后端 | 检查 Ollama 是否 `serve` |
| `backend_timeout` | 3 | 超时（重试耗尽） | 降并发/加超时/换小模型 |
| `http_status_error` | 3 | 非重试 4xx 或重试耗尽 | 看 `detail.status` |
| `protocol_error` | 4 | 响应不符合 chat 契约 | 升级后端或改适配 |
| `response_too_large` | 4 | 响应超预算 | 收窄 `max_tokens`/预算 |
| `empty_response` | 4 | 空补全 | 视为失败，不要填空串 |
| `json_extraction_error` | 5 | 抽不出 JSON 对象/数组 | 重跑或改进 Prompt，**不要猜** |

### 证据日志（可选）

每次成功调用追加一行 JSONL（默认**只存哈希**，便于审计又不复制客户数据）：

```json
{"kind":"completion","ts":"...","host":"127.0.0.1:11434","model":"qwen2.5:1.5b",
 "latency_s":1.23,"attempts":1,"usage":{"completion_tokens":18},
 "prompt_sha256":"…","response_sha256":"…","response_bytes":52}
```

JSON 契约失败会额外记一条 `{"kind":"json_extraction_failure", …}`——审计能看见"哪一次抽取被判死"，但看不到正文。

## 在确定性架构中的位置

```text
自然语言题干
   │
   ▼
┌───────────────────┐   ← 本仓库（感知层，唯一允许"概率"的地方）
│ local-llm-gateway │   只做：发出请求 / 收文本 / 严格 JSON / 记录证据
└─────────┬─────────┘
          ▼
   Pydantic 契约层       ← 字段、类型、必填、Decimal 化（不合契约即拒）
          ▼
   确定性算子 / 看门狗   ← 金额、勾稽、断言（模型不参与）
          ▼
   底稿池 / 审计链       ← 哈希链留痕，可复核
```

三条红线：

1. **模型不算金额**，算数一律进确定性算子；
2. **不合契约即失败**，不"尽量解析"；
3. **失败可复现**：错误码 + 证据哈希 + 尝试次数都在日志里。

## 兼容性

任何 OpenAI 兼容端点：

```text
Ollama            http://127.0.0.1:11434/v1
vLLM              http://127.0.0.1:8000/v1
llama.cpp server  http://127.0.0.1:8080/v1
LM Studio         http://127.0.0.1:1234/v1
云端兼容服务       https://<provider>/v1   （配 LLM_GATEWAY_API_KEY）
```

## 安全边界

- API Key 只放请求头；异常消息、证据日志、CLI 输出均不含 Key（有测试覆盖）；
- 默认只连接你配置的 `base_url`，无遥测、无其他外联；
- 证据日志默认只存 SHA-256，不存题干与响应正文；
- 大响应/大 prompt 有硬预算，避免拖死主流程。

## 测试

```powershell
python -m unittest discover -s tests -v     # 49 passed，全程离线（本地假服务器）
```

测试覆盖：契约、超时/重试策略、4xx 不重试、响应超限、空补全、JSON 严格性、
证据哈希、Key 泄漏防护、CLI 退出码。

## English summary

`local-llm-gateway` is a zero-dependency, fail-closed client for any
OpenAI-compatible LLM endpoint (Ollama, vLLM, llama.cpp, LM Studio, cloud).
It is the "perception layer" of a deterministic finance architecture: the
model is only allowed to *read* (extract JSON), never to *compute*. Bad model
output fails loudly (`json_extraction_error`) instead of being hallucinated
around. Optional JSONL evidence stores SHA-256 hashes, latency and retry
counts — not the client's text.

## 许可

MIT © 2026 Yin Kang
