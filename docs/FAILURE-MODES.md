# 失败模式矩阵（Fail-closed 行为表）

> 原则：**任何失败都显式抛出，绝不返回猜测值。** 调用方可以重试、降级到更小模型、
> 或转人工，但不能"继续往下算"。

| # | 触发条件 | 异常类 | `code` | CLI 退出码 | 是否重试 | 调用方建议动作 |
|---|---|---|---|---|---|---|
| 1 | base_url 非法 / 参数越界 | `ConfigError` | `config_error` | 2 | 否 | 修配置 |
| 2 | 题干字节超预算 | `PromptTooLarge` | `prompt_too_large` | 2 | 否 | 拆分题干或提高预算（需评审） |
| 3 | 后端连不上（服务未起/端口错/DNS） | `BackendUnreachable` | `backend_unreachable` | 3 | 是（有界） | 检查 `ollama serve` / `ollama list` |
| 4 | 超时 | `BackendTimeout` | `backend_timeout` | 3 | 是（有界） | 换小模型 / 缩短 prompt / 调超时 |
| 5 | 4xx（非 408/429） | `HTTPStatusError` | `http_status_error` | 3 | 否 | 看 `detail.status` 与 `body_excerpt` |
| 6 | 5xx / 408 / 429 重试耗尽 | `HTTPStatusError` | `http_status_error` | 3 | 是（有界） | 后端过载：降并发/稍后重试 |
| 7 | 响应非 JSON / 缺 `choices` / 缺 `content` | `ProtocolError` | `protocol_error` | 4 | 否 | 后端不兼容：换端点或加适配 |
| 8 | 响应字节超预算 | `ResponseTooLarge` | `response_too_large` | 4 | 否 | 收窄输出（max tokens / prompt 约束） |
| 9 | 空补全 / 全空白 | `EmptyResponse` | `empty_response` | 4 | 否 | 视为失败；检查模型是否被卸载 |
| 10 | 抽不出 JSON 对象/数组 | `JSONExtractionError` | `json_extraction_error` | 5 | 否 | 重跑 / 改 prompt / 显式 `allow_prose` |
| 11 | 证据文件不可写 | `OSError`（向上抛） | — | 1 | 否 | 修目录权限；**不允许静默丢弃证据** |

## 明确不做的事（反模式）

| 反模式 | 为什么禁止 |
|---|---|
| 空补全时返回 `{}` | 会把"没抽到"伪装成"抽到了空" |
| 超时后返回上一次结果 | 串题，审计无法区分 |
| JSON 解析失败时"用正则硬抠数字" | 幻觉进入确定性层的典型路径 |
| 把 API Key 写进错误消息 | 泄漏；有测试专门覆盖 |
| 证据日志落全文题干 | 把审计文件变成客户数据副本 |
| 对 401/403 自动重试 | 无意义放大，掩盖鉴权问题 |

## 与主流程的接口约定

调用方拿到 `GatewayError` 后，只允许三种处理：

1. **重试一次**（仅 `backend_*` 类，且总次数有上限）；
2. **降级**（换更小模型 / 更短题面），但**不允许**放宽 JSON 契约；
3. **转人工或中止**，写底稿池 `outcome=failed`，保留证据哈希。

任何情况下，`json_extraction_error` 都不得被吞掉后继续算账。
