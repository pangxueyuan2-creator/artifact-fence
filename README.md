# Artifact Fence

> **在 GitHub Actions 上传前，证明一个 artifact 实际会带走哪些文件。**

[![CI](https://github.com/pangxueyuan2-creator/artifact-fence/actions/workflows/ci.yml/badge.svg)](https://github.com/pangxueyuan2-creator/artifact-fence/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

CI 常见写法 `path: build/**` 很方便；它在显式或运行时启用 hidden files 时可能把 `.env`、私钥、临时凭据或调试快照一起上传。**Artifact Fence** 是一个只读的本地 CLI：它解析 GitHub Actions 的 artifact upload step，在当前工作树中按已知静态语义展开路径，并用不会回显匹配值的报告把暴露面交给你审查或阻止。

它不是通用 secrets scanner，也不执行 workflow、shell 或仓库代码。它回答一个更窄的问题：

> 这个 GitHub Actions 上传步骤，**现在会上传什么**？

## 60 秒演示

要求 Python 3.11+。从源码运行：

```bash
git clone https://github.com/pangxueyuan2-creator/artifact-fence.git
cd artifact-fence
python -m pip install -e .
bash demo/run_demo.sh
```

演示 fixture 使用 `build/**` 且显式设置 `include-hidden-files: true`；它上传 `build/results.txt` 的同时也包含 `build/.env`。输出不会显示 `.env` 内的测试 token；`check` 会以退出码 `1` 阻止门禁。

```text
ARTIFACT test-diagnostics: .github/workflows/ci.yml / test / Upload diagnostics
  includes: build/.env
  includes: build/results.txt
HIGH sensitive-filename (build/.env): Artifact includes a filename commonly used for credentials or environment secrets.
HIGH credential-assignment (build/.env): Artifact file matches a credential-shaped pattern; value is intentionally not shown.
```

## 使用方法

### 扫描，不改变退出码

```bash
artifact-fence scan .
artifact-fence scan . --format json > artifact-fence.json
artifact-fence scan . --workflow .github/workflows/release.yml
```

`scan` 即使存在 findings 也返回 `0`，适合先观察和落盘审计证据。

### 在 CI 中门禁

```bash
artifact-fence check . --min-severity high
```

`check` 的退出码如下。

| 退出码 | 含义 |
|---:|---|
| `0` | 没有达到阈值的 finding。 |
| `1` | 存在达到 `--min-severity` 的 finding。 |
| `2` | 参数、根目录或 workflow 解析错误。 |

一个最小 GitHub Actions 接入：

```yaml
- name: Install Artifact Fence
  run: python -m pip install artifact-fence

- name: Fence upload scope
  run: artifact-fence check . --min-severity high
```

## 当前检测内容

| 检测 | 严重度 | 含义 |
|---|---|---|
| `sensitive-filename` | high | 拟上传文件名类似 `.env`、`.npmrc`、`.netrc`、私钥或 credentials 文件。 |
| `credential-assignment` | high | 文本文件存在凭据形态的 `TOKEN=...`、`SECRET: ...` 等赋值；值绝不回显。 |
| `private-key` / `github-token` / `aws-access-key` | high | 文本文件匹配常见私钥或 token 形态；值绝不回显。 |
| `unsafe-artifact-path` | high | upload path 为绝对、home-relative 或含 `..` 的路径。 |
| `dynamic-artifact-path` | medium | upload path 含表达式/命令替换，静态分析不会猜测展开结果。 |
| `sensitive-filename-absent` | high | upload path 字面声明了 `.env` 类凭据文件名，但工作树中尚不存在；内容无法审查，CI 可能在执行时生成后上传。 |
| `artifact-path-not-present` | info | 当前工作树没有匹配的**非隐藏**文件，可能由 CI 执行时生成。 |
| `artifact-file-too-large` | info | 为限制资源消耗，只检查大文件的前 1 MiB；报告不保证覆盖其余内容。 |
| `artifact-enumeration-truncated` | high | 工作树枚举超过安全上限，报告不完整；`check` fail-closed。 |
| `artifact-symlink-skipped` | info | 选中了指向仓库内的符号链接，但为避免链接语义不确定性未遍历其目标。 |
| `dynamic-include-hidden-files` | medium | `include-hidden-files` 为动态表达式；静态枚举会保守地**包含**隐藏文件，避免漏报。 |

Artifact Fence 支持 `actions/upload-artifact` 与 `actions/upload-pages-artifact`；多行 path 和 `!` 排除模式会按顺序解释，目录路径会递归展开。为了避免 action ref 或运行时表达式造成 hidden-file 漏报，**只有显式静态的 `include-hidden-files: false` 才会排除隐藏文件**；省略、动态、浮动或 Pages 情况都按“可能上传”枚举。`upload-pages-artifact` 省略 path 时使用 `_site/` 默认目录。绝对、UNC、Windows drive 或 `..` 越界路径会被拒绝为 high finding。

## JSON API

CLI 是稳定的 JSON 边界；同时提供一个小型 Python API：

```python
from artifact_fence import has_severity, scan_project

report = scan_project(".")
print(report.to_dict())
if has_severity(report, "high"):
    raise SystemExit("artifact fence failed")
```

`ScanReport.to_dict()` 的顶层带 `schema_version: 1`，可安全用于 CI 后续处理。

## 它与相邻工具的关系

Artifact Fence 保持独立，是因为它既不是 agent policy 编译器，也不是改动证据 gate，更不是 Issue-to-PR agent。

| 工具 | 回答的问题 | Artifact Fence 的不同点 |
|---|---|---|
| [GuardSpec](https://github.com/pangxueyuan2-creator/guardspec) | 代理开始工作前，路径/命令/网络/MCP 是否被仓库规则允许？ | 不解析或执行 CI upload step。 |
| [TaskToPR](https://github.com/pangxueyuan2-creator/tasktopr) | 如何把一个 Issue 变成隔离、测试过的 PR？ | 不创建分支、调用模型或打开 PR。 |
| [PatchWitness](https://github.com/pangxueyuan2-creator/patchwitness) | 已有改动是否遵循范围、保护路径和实跑检查的证据？ | 不展开 artifact 文件集合或扫描上传暴露面。 |
| [repo-privacy-guard](https://github.com/pangxueyuan2-creator/repo-privacy-guard) | 仓库在公开前是否有疑似秘密/隐私风险？ | 只分析 Actions 上传语义，而不是全仓库公开风险。 |

完整研究、20+ 真实痛点、候选评分和边界判定见 [`docs/research-and-decision.md`](docs/research-and-decision.md)。

## 限制

Artifact Fence 是 **static analysis，不是运行时证明**。它不会执行 workflow、shell、JavaScript、Docker、下载器或表达式。含 `${{ ... }}`、`$()`、`${...}` 的路径只会被报告为 dynamic。它只能列出当前工作树中已存在的文件；远程 runner 在运行时生成、下载或解压的文件无法被完整预测。对于 hidden files，工具故意选择潜在上传的上界：除非 `include-hidden-files` 是显式静态 `false`，否则隐藏文件会进入枚举，这可能带来保守的 false positive。为控制资源消耗，workflow YAML 最大读取 1 MiB，制品内容的凭据形态检测最多读取每个文件的前 1 MiB；整个工作树最多枚举 10,000 个条目、每个 artifact 最多纳入 5,000 个文件。上限触发时返回 high finding 并 fail-closed。

启发式凭据检测会有 false positive 和 false negative，不能替代密钥轮换、最小权限、秘密管理或完整的敏感信息扫描。当前 MVP 只理解两个 GitHub 官方 upload action，不支持 composite action 内部上传、第三方上传 action、GitLab CI、CircleCI 或 upload artifact 的 retention/access 配置。为避免 root escape 与无限递归，目录符号链接从不跟随：指向仓库外的链接使 gate 失败，指向仓库内的链接会以 info finding 报告为未遍历。它还没有模拟所有 action 版本、动态 `if` 条件、工作区外生成器和运行时表达式；因此报告是当前工作树对已知静态配置的审计证据，而不是运行时安全保证。

## 开发

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src
```

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请不要开公开 Issue，参阅 [SECURITY.md](SECURITY.md)。本项目采用 [MIT License](LICENSE)。
