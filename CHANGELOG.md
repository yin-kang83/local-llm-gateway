# 变更记录

格式：Keep a Changelog；版本号：语义化版本。

## [0.1.0] - 2026-11-27

### Added

- `Gateway` 门面：`complete()` / `complete_json()` / `check()` / `benchmark()`；
- `OpenAICompatibleBackend`：零第三方依赖的 `urllib` 实现，支持 Ollama / vLLM /
  llama.cpp server / LM Studio / 任何 OpenAI 兼容端点；
- 有界重试：超时 / 408 / 425 / 429 / 5xx，指数退避 + 抖动；其余 4xx 不重试；
- 客户端预算：`max_prompt_bytes` / `max_response_bytes`（超限即失败，不发请求/不读满）；
- `json_mode`（env `LLM_GATEWAY_JSON_MODE=1` / CLI `--json-mode`）：向支持的后端发送
  `response_format={"type":"json_object"}`；**只是提示**，抽完仍走严格 JSON 校验；
- 严格 JSON 抽取：整体 JSON 对象/数组，或 Markdown fenced `json` 块；
  默认不做"从废话里抠第一个大括号"的兜底（`allow_prose=True` 显式开启）；
- 证据日志：JSONL 追加写（`fsync`），默认只存 SHA-256 + 延迟 + 尝试次数 + usage，
  不存题面/响应正文；
- CLI：`check` / `run` / `bench` / `version`，稳定退出码（0/2/3/4/5）；
- `bench`：延迟分布（min/median/max）+ tokens/s（仅在后端返回 usage 时）+ 硬件信息；
- 51 项离线单元测试（本地假服务器，无网络、无 API key）；
- `tools/prepublish_check.py`：出域前自检（密钥/大文件/禁止类型/本地路径/必需文件）；
- 文档：架构决策、失败模式矩阵、本机部署实测、发布流程（Gitee → GitHub → Zenodo）。

### Release

- Zenodo v0.1.0：version DOI `10.5281/zenodo.24574389`，concept DOI `10.5281/zenodo.24574388`；
- 发布包：`local-llm-gateway-0.1.0.zip`（git archive of tag `v0.1.0`）+ `SHA256SUMS.txt`；
- Gitee 为源仓，GitHub 为镜像；MIT。

### Documentation

- `docs/NEGATIVE-RESULTS.md`：把本接入层接入确定性流水线后记录到的 5 类真实模型失败
  （指令违背 / 结构伪造 / 表达式算术 / 不平分录 / 错金额导致账面为负）及每层闸门的拦截结果，
  并给出"Prompt 不是契约"的设计规则与论文可用表述。

### Security

- API Key 只进请求头；异常消息、`detail`、证据、CLI 输出均不含 Key（有测试覆盖）；
- 默认只连接配置的 `base_url`，无遥测、无额外外联；
- 证据日志可选 `evidence_store_text=1`，默认关闭。

### Known limitations

- 本机实测（i5-8250U / 7.9 GB / CPU，`qwen2.5:1.5b` Q4_K_M）：探针 0.85 s、14.0 tokens/s；
  长投题面严格 JSON 热态约 3.5 s、冷启动 10.9 s、模型峰值内存约 1.16 GB（详见 `docs/LOCAL-SETUP.md`）；
- 本地小模型可能无视 JSON 指令（实测），需配合 `json_mode`，且仍可能抽参失败——这是设计预期；
- `temperature=0` 不保证跨后端逐字节确定（确定性最终由下游契约层 + 算子保证）；
- 不含 token 计数与上下文窗口管理（`.tokenize` 属后端职责）；
- 无内置并发池（并发由上层调度决定）。
