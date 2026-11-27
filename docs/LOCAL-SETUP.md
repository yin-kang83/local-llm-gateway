# 本地部署实测（本机基线）

> 目的：记录"这套东西在这台机器上到底能不能跑、跑多快"，避免只凭感觉评估。
> 数据由 `llm-gateway bench` 实测生成；机器变了就重跑并更新本文件。
> 采集日期：**2026-11-27**；采集方式见每节命令。

## 1. 本机硬件

| 项 | 值 |
|---|---|
| CPU | Intel Core i5-8250U（4 核 8 线程） |
| 内存 | 7.9 GB DDR4-2133（板载，不可升级） |
| GPU | NVIDIA MX150 2 GB（另有 Intel UHD 620 核显） |
| 部署位置 | 便携版目录自选（下文记作 `<OLLAMA_DIR>`）；模型默认落 `%USERPROFILE%\.ollama` |

## 2. 运行时与模型

| 项 | 值 |
|---|---|
| Ollama | v0.40.18（Windows amd64 便携包，`ollama serve`） |
| 包校验 | SHA-256 `ca97292a…7cc4c16`（与 GitHub release digest 一致） |
| 模型 | `qwen2.5:1.5b`，Q4_K_M，986 MB，上下文 32768 |
| 推理设备 | **CPU**（见 §5 的 CUDA 说明） |
| Python | 3.12.14 |

## 3. 实测数据

### 3.1 自检（`llm-gateway check`）

| 指标 | 值 |
|---|---|
| 后端可达 + 模型存在 | `model_available=true` |
| `/v1/models` 往返 | **0.056 s** |

### 3.2 测速（`llm-gateway bench --n 5 --warmup 1`）

探针 prompt 固定（85 B，要求只输出一行 JSON）：

| 指标 | 值 |
|---|---|
| 单次延迟 min / median / max | **0.841 s / 0.852 s / 0.873 s** |
| 5 次总延迟 | 4.278 s |
| 输出 token | 60（12/次） |
| **吞吐** | **14.03 tokens/s** |
| 尝试次数 | 5（无重试） |

> 稳定性很好：min↔max 仅差 32 ms，说明短输出下 CPU 推理可预测。

### 3.3 真实抽参任务（长投题面，`examples/finance_extraction.py`）

题面约 120 字、要求输出 4 字段 JSON，`json_mode=true`：

| 场景 | 实测 |
|---|---|
| **冷启动**（模型未加载 → 首次完整调用） | **10.91 s** |
| 热态第 1/2/3 次 | 3.38 s / 3.51 s / **3.78 s** |
| 输出质量 | 契约校验通过：`initial_cost=1000`、`ownership_pct=30`、`investee_net_profit=500` |

结论：**1.5B 在这台 8 GB 机器上做"短题面 → 严格 JSON 抽参"是可用的**（热态 3.5 s 左右）；
冷启动多 ~7 s 是模型加载成本，常驻服务后不影响。

### 3.4 内存

| 指标 | 值 |
|---|---|
| 模型进程峰值（推理时） | **约 1.16 GB** |
| 系统可用内存最低值（bench 期间） | 约 0.72 GB（台面程序较多时） |
| 模型未加载时 ollama 服务 | ~28 MB |

> 7.9 GB 机器同时开着微信/浏览器/IDE 时会比较紧张，但不至于 OOM；
> 若要把 1.5B 换成 3B，需要先腾内存。

## 4. 关键发现：为什么必须 `json_mode` + fail-closed

同一份 system 指令下（要求"只输出 JSON"）：

| 模式 | 模型实际返回 | 结果 |
|---|---|---|
| 纯 Prompt（无 `response_format`） | `借：长期股权投资 1100 贷：银行存款 1000 投资收益 100` | 不是 JSON → **`json_extraction_error`，exit 5** |
| `response_format={"type":"json_object"}` | `{"initial_cost": "1000", "ownership_pct": "30", ...}` | 契约通过 |

即：**小模型会不听话**。所以本接入层做两件事——

1. `json_mode` 给后端一个 JSON 约束提示（Ollama/vLLM 等支持 `response_format` 时生效）；
2. 抽完仍然做严格 JSON 校验，照样可能失败（fail-closed），**不猜、不兜底**。

这是"本地小模型能用"的前提：不是模型更乖，而是外面有一圈硬闸门。

## 5. 已知坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 首次调用报 HTTP 500：`CUDA error ... PTX was compiled ...` | MX150 驱动太旧，Ollama 的 CUDA 后端 PTX 不兼容 | 强制 CPU：启动服务前设 `CUDA_VISIBLE_DEVICES=-1`、`OLLAMA_NUM_GPU=0`（可选 `OLLAMA_LLM_LIBRARY=cpu`），重启 serve |
| 下载 `github.com:443` 超时 | 直连时通时断 | 用镜像前缀下载同一 URL（如 `gh-proxy.com`），并用官方 SHA-256 校验 |
| `ollama pull` 首次很慢（~7 MB/s 量级） | 官方 registry 直连 | 可接受；失败即停，不换源硬凑 |
| 控制台中文乱码 | Windows GBK 控制台 | 输出文件用 UTF-8；Python 加 `PYTHONIOENCODING=utf-8` |
| `check` 返回空模型列表报 `protocol_error` | 服务起来了但模型没拉 | 先 `ollama pull` 再 `check` |
| 冷启动慢 | 模型加载进内存 | bench 默认 warmup=1，不计入统计；服务常驻即可 |

## 6. 复现命令（本机原文）

```powershell
# 启动（CPU 模式）
$env:OLLAMA_MODELS = '<MODELS_DIR>'
$env:CUDA_VISIBLE_DEVICES = '-1'
$env:OLLAMA_NUM_GPU = '0'
<OLLAMA_DIR>\ollama.exe serve

# 拉模型
<OLLAMA_DIR>\ollama.exe pull qwen2.5:1.5b

# 接入层
$env:LLM_GATEWAY_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:LLM_GATEWAY_MODEL = 'qwen2.5:1.5b'
$env:LLM_GATEWAY_JSON_MODE = '1'
python -m local_llm_gateway check --json
python -m local_llm_gateway bench --n 5 --json
python examples/finance_extraction.py
```
