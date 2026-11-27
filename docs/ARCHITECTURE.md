# 架构与设计决策

## 1. 分层

```text
调用方（抽参流程 / SuperAgent / 任意脚本）
        │  prompt: str
        ▼
┌─────────────────────────────────────────────────────┐
│ Gateway (gateway.py)                                │
│  · 配置装配（env → GatewayConfig）                  │
│  · 消息构造（system/user）                          │
│  · 严格 JSON 契约（extraction.py）                  │
│  · 证据记录（哈希 + 延迟 + 尝试次数）               │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────┐
│ OpenAICompatibleBackend (backends.py)               │
│  · urllib 直连，零第三方依赖                        │
│  · 预算（prompt/response 字节）                     │
│  · 有界重试（超时/408/429/5xx）                     │
│  · 契约校验（choices → message → content）          │
└─────────────────────────────────────────────────────┘
                     ▼
      任意 OpenAI 兼容后端（本地/云端）
```

## 2. 关键决策（为什么这样做）

### D1 只用标准库，零依赖

抽参层是整个系统里最不该"引依赖"的一层：依赖越少，供应链面越小，离线部署越简单，审计越容易。
`urllib` 足以覆盖 `POST /chat/completions` 与 `GET /models`。代价是自己实现重试与超时——但这恰好是我们要控制的部分。

### D2 fail-closed，而不是"尽力返回"

- 层不齐、类型不对、超预算、空补全 → **抛错**；
- JSON 抽取只接受两种形态：整体是 JSON 对象/数组，或 Markdown fenced `json` 代码块；
- 默认**不做**"从废话里抠第一个大括号"的兜底（可用 `allow_prose=True` 显式打开，且只抠一个平衡块）。

理由：确定性下游一旦拿到半截 JSON，错误会以"看起来合理"的形式流进底稿池，事后极难追。
宁可让这一步失败，让人或调用方重试。

### D3 有界重试，只重试"可恢复"的

| 情况 | 行为 |
|---|---|
| 超时 / 连接失败 | 重试，指数退避（0.5s 起，上限 8s） |
| 408 / 429 / 5xx | 重试 |
| 其余 4xx | **不重试**（参数/鉴权问题重试无意义） |
| 协议错误 / 超预算 / 空补全 | **不重试**（重试只会重复浪费） |

`attempts` 会记进完成对象和证据日志，便于事后判断后端稳定性。

### D4 预算在客户端先卡

`max_prompt_bytes` / `max_response_bytes` 在发请求前/读响应时强制。
大 prompt 或超大响应不应该"把主流程拖死"，更不应该把内存打爆。

### D5 证据只存哈希

证据文件的价值是证明"调用发生过、用了哪个模型、延迟多少、重试几次、输入输出是否被改"，
而不是保存客户数据副本。SHA-256 + 元数据足够做审计链的叶子节点；需要正文时由调用方在自己的底稿池里管。

### D6 密钥只从环境变量/内存来

`api_key` 只出现在 `Authorization` 头里。异常消息、`detail`、证据日志、CLI 输出都不引用它（有测试）。

### D7 temperature 默认 0

抽参任务要的是稳定，不是创意。默认 `temperature=0.0`；需要多样性时显式传参。
（`temperature=0` 不保证跨后端逐字节确定，故"确定性"最终由下游 Pydantic + 算子保证，而非靠模型。）

## 3. 数据流：一次 `complete_json`

```text
prompt
  → 组装 messages（可选 system）
  → prompt 字节预算检查            （失败：prompt_too_large, exit 2）
  → POST /v1/chat/completions
      → 连接失败/超时/5xx         （重试 → 耗尽：backend_*, exit 3）
      → 非 4xx 错误               （http_status_error, exit 3）
  → 响应字节预算检查               （失败：response_too_large, exit 4）
  → JSON 解析 + 契约校验           （失败：protocol_error, exit 4）
  → 内容非空检查                   （失败：empty_response, exit 4）
  → 严格 JSON 抽取                 （失败：json_extraction_error, exit 5）
  → 写证据（哈希）
  → 返回 (data, Completion)
```

## 4. 不在这里做的事

- **不**做模型加载/量化/训练（那是 Ollama 及其模型的事）；
- **不**做业务 schema 校验（调用方用 Pydantic/dataclass 定义自己的 T-box）；
- **不**做缓存（缓存语义应由调用方按业务幂等键决定，避免"抽参结果串题"）；
- **不**做并发编排（并发属于上层调度，本层只保证单次调用可预期）。
