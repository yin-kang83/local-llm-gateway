# 发布流程（Gitee → GitHub → Zenodo）

> 本仓库是**公开 MIT 项目**，发布前必须过"出域门"。本文件是发布检查单，
> 任何一步不过就停，不"先发再补"。

## 0. 版本与内容边界

- 版本号：语义化 `MAJOR.MINOR.PATCH`；首个公开版 `0.1.0`。
- 允许出域：代码、测试、文档、bench 方法与本机**硬件级**数据（CPU 型号/内存/延迟）。
- 不允许出域：客户底稿、真实题面、内部仓库路径、内网地址、任何密钥、对话原文。
- 本仓库**不含模型权重**（权重由 Ollama 分发，仓库只写 `ollama pull` 命令）。

## 1. 发布前自检（本地，必须全绿）

```powershell
# 1) 测试（离线）
python -m unittest discover -s tests -v

# 2) 出域扫描（本仓库自带）
python tools/prepublish_check.py --dir .

# 3) 大文件检查（> 2 MB 不允许入库）
# 4) 密钥检查（grep 级别：ghp_/github_pat_/sk-/Bearer）
```

## 2. 出域门（独立复核，不能自检代替）

发布动作前，用**独立扫描工具**再扫一遍（使用内部既有的 DLP 外发扫描工具，不随本仓库发布），
并按既有流程签发《出域放行单》：

| 检查类 | 要求 |
|---|---|
| P1 本地路径/盘符（Windows 驱动器路径、UNC 路径） | 0 命中 |
| P2 内部存储名（不上公开清单） | 0 命中 |
| P3 内部系统名 | 0 命中 |
| P4 客户/真实公司名 | 0 命中 |
| P5 个人身份信息 | 0 命中 |
| P6 密钥/令牌 | 0 命中 |
| P9 大文件/二进制 | 0 命中 |
| P10 `.env` / `.key` | 0 命中 |

## 3. Gitee

```powershell
# 1) 建仓（API 或网页），私有先建，扫描通过后再转公开
# 2) 本地首次推送
git add -A
git commit -m "release: local-llm-gateway v0.1.0"
git remote add origin https://gitee.com/yin-kang83/local-llm-gateway.git
git push -u origin main
# 3) 验证
git ls-remote --heads origin
```

## 4. GitHub（镜像）

```powershell
# GitHub 仓库仅作镜像；Gitee 为源
git remote add github https://github.com/yin-kang83/local-llm-gateway.git
git push github main
# 验证（令牌只在内存/命令行，不写入 .git/config）
```

## 5. Zenodo（DOI）

1. 打 release 包：`git archive --format=zip --prefix=local-llm-gateway-0.1.0/ -o local-llm-gateway-0.1.0.zip HEAD`；
2. 生成 `CHECKSUMS.txt`（SHA-256），随包上传；
3. 元数据：
   - title：`local-llm-gateway: a local-first, fail-closed OpenAI-compatible LLM gateway`
   - creators：Yin Kang
   - license：MIT
   - type：software
   - keywords：LLM gateway, Ollama, local-first, fail-closed, audit, deterministic finance
4. 上传后拿 **concept DOI** 与 **version DOI**；
5. 回填 `CITATION.cff`（`doi:` 与 `identifiers:`）与 README 徽标；
6. 提交 "docs: add Zenodo DOI" 并同步 Gitee/GitHub —— **DOI 回填后再打一次 tag**。

## 6. 发布后

- 记录：Gitee commit / GitHub commit / Zenodo DOI / 包 SHA-256 / 测试数；
- 更新记忆仓库日志与项目状态；
- 不允许"发布后改代码只推 GitHub 不推 Gitee"（源必须在 Gitee 保持最新）。

## 7. 一票否决项

- 测试有红；
- 出域扫描有命中；
- 证据日志/示例里出现真实题面或客户名；
- 仓库里出现 `.env`、`*.key`、模型权重、数据库；
- 文档里写了内部路径或内部仓库名。
