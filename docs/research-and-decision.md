# Artifact Fence：GitHub 调研与孵化决策

**调研日期：2026-08-15。** 本记录优先使用 GitHub 的 Issue、Discussion、关闭状态与代码库文档，而非星标数。原始 JSON 证据、搜索结果和相邻项目的只读克隆保存在本次孵化工作区；本文件只保留可复查的结论和直接链接。

## 研究样本

| 领域 | 代表性项目 | 使用方式 | 得到的信号 |
|---|---|---|---|
| AI coding agents | OpenAI Codex、Aider | Issue 与自动化失败案例 | Agent 进入无人值守流程后，失败必须可诊断、可被 CI 正确判错。 |
| MCP | MCP TypeScript SDK、MCP Inspector、FastMCP | Issue、Discussion、官方 Inspector 文档 | 协议及工具面快速演化；认证、动态表面、结果大小、记录和来源仍在建设中。 |
| CI/CD | actions/runner、Renovate | Issue | Runner 和依赖自动化在失败、排队、清理和回滚时不一定留下可行动证据。 |
| Code review / testing | reviewdog、Playwright | Issue | 大规模 diff、路径解析与异步工具调用都可能让“绿色”结果失真。 |
| Supply-chain / agent security | OpenSSF Scorecard、TruffleHog、MCP 规范讨论 | Issue 与 Discussion | 现有检查重仓库/依赖/密钥，较少以**实际会被 CI 上传的制品集合**为审计单位。 |

## 至少 20 项真实痛点

下表中的每项都来自公开 GitHub 讨论或 Issue；“影响”是本次调研的产品归纳，而不是对原作者的额外归因。

| # | 真实痛点 | 证据 | 对开发者的影响 |
|---:|---|---|---|
| 1 | Aider 在致命 API 连接错误时仍退出为 `0`。 | [Aider #5552][1] | Headless 自动化把失败误判为成功。 |
| 2 | 大型仓库中，任意交互都可能触发全仓扫描。 | [Aider #5529][2] | 代理迭代在大仓库中失去可预测延迟。 |
| 3 | Codex CLI 的 MCP 工具列出来但未注入模型可用工具集。 | [Codex #38689][3] | “已配置”的工具与“可执行”的工具面不一致。 |
| 4 | Codex 定时自动化在加载工作区依赖时卡住，而前台任务正常。 | [Codex #38671][4] | 手工可用不代表无人值守可用。 |
| 5 | MCP 服务器可在 `tools/list` 声明输入 schema，却不在 `tools/call` 强制它。 | [MCP TypeScript SDK #2628][5] | 声明的合同不能自动成为执行边界。 |
| 6 | 工具表变更可在调用进行中替换输出 schema 并导致客户端错误。 | [MCP TypeScript SDK #2612][6] | 动态工具面使回归和复现变难。 |
| 7 | SDK 文档中 `z.object()` schema 的失败可能沉默或报错晦涩。 | [MCP TypeScript SDK #2627][7] | 开发者需要把协议配置实际跑通，而非只读配置。 |
| 8 | FastMCP HTTP transport 被报告可允许未认证访问 MCP 工具。 | [FastMCP #4734][8] | 工具端点的默认暴露面必须被明确审计。 |
| 9 | FastMCP 用户请求在任务上下文快照中支持对凭据和 HTTP 头脱敏或加密。 | [FastMCP #4747][9] | 调试/可观测性制品本身可能泄露认证数据。 |
| 10 | FastMCP 的 transient HTTP error 后，状态代理客户端不能恢复。 | [FastMCP #4825][10] | 失败后的人工重跑成为常规 workaround。 |
| 11 | Playwright MCP 的首个 `tools/call` 会因客户端未开 SSE 而停顿约一分钟。 | [Playwright #42256][11] | 表面成功的配置存在高延迟失效模式。 |
| 12 | Playwright 的一次 `browser_navigate` 成功后，下一次工具调用仍得到 `about:blank`。 | [Playwright #42188][12] | 单步成功不能证明后续状态正确。 |
| 13 | 自托管 Actions runner 通信停止后，作业被清理而诊断信息不保留。 | [actions/runner #4632][13] | 维护者只能依赖不完整日志排障。 |
| 14 | Ephemeral runner 在丢失任务分配后可能残留为 ghost。 | [actions/runner #4617][14] | CI 资源与任务状态发生漂移。 |
| 15 | reviewdog 在超过 300 个文件的 PR 上可能无法工作。 | [reviewdog #2150][15] | 大改动中的自动审查覆盖会静默退化。 |
| 16 | reviewdog 的 SARIF 相对路径解析不正确时无法创建 review。 | [reviewdog #2480][16] | 结果位置错误使修复工作需要人工重新定位。 |
| 17 | Scorecard 的 Packaging 检查需要不断追赶新的发布机制。 | [OpenSSF Scorecard #5168][17] | 供应链检查容易滞后于实际发布面。 |
| 18 | OpenSSF 社区明确提出 agent security checks 的需求。 | [OpenSSF Scorecard #4982][18] | AI agent 项目的安全控制仍缺少成熟通用门禁。 |
| 19 | Renovate 的 release-age 检查能使 digest 与锁文件维护 PR 永久 pending。 | [Renovate #45236][19] | 自动化需要状态证据，而非只给“正在等候”。 |
| 20 | TruffleHog 的 GitHub Action 未把 Actions artifacts 纳入 GitHub scanner。 | [TruffleHog #5205][20] | 仓库源代码扫描与 CI 实际导出数据之间存在盲区。 |
| 21 | TruffleHog 的未知 GitHub Action event 会静默退化为全历史扫描。 | [TruffleHog #5190][21] | 事件上下文丢失会扩大运行成本并改变扫描语义。 |
| 22 | 社区提出 MCP Tool Bill of Materials，以解决工具来源和供应链可追溯性。 | [MCP Discussion #2189][22] | 工具面需要一种可携带、可比较的身份记录。 |
| 23 | 社区讨论多步骤 MCP workflow 的 portable execution record。 | [MCP Discussion #2493][23] | 执行记录尚缺少默认可移植的证据格式。 |
| 24 | MCP 规范讨论数百工具导致 tool bloat。 | [MCP Discussion #2036][24] | 开发者必须先看清实际暴露面，才有机会治理它。 |
| 25 | MCP 规范讨论 agent identity 与 delegation。 | [MCP Discussion #2404][25] | 代理代表谁执行与授权链仍未标准化。 |

> **反复出现的模式**：CI、调试与代理编排都会产生大量“看似临时”的输出；但日志、快照、诊断包和测试输出一旦被上传为 artifact，便成为新的供应链和隐私暴露面。现有工具通常在仓库源、依赖或运行时网络层处理问题，较少静态证明一个 GitHub Actions 上传步骤**现在会打包哪些文件**。

## Discussions、人工 workaround 与被拒绝/未合并方向

MCP Discussion 中，工具清单、执行记录、身份与 TBOM 均仍以提案形式存在，说明协议层尚未提供默认实现。[22] [23] [24] [25] FastMCP 的凭据快照请求和短暂故障不恢复问题也揭示常见 workaround：减少记录、手动清空状态或重跑任务。[9] [10] Aider 的错误退出码案例则显示调用方往往不得不自行包一层日志/退出码校验。[1]

对于候选方向，检索 `mcp contract testing`、`mcp replay testing` 与 `mcp security testing` 的公开仓库后，未发现成熟的、可替代的通用 CI 制品上传范围验证器；MCP replay 方向已有同名项目，故不进入实现阶段。MCP lockfile 方向已有 runtime proxy（mcptrust），其定位是运行时策略、签名和遥测，功能显著重于本项目的 GitHub Actions 静态制品边界。[26] 这既避免了重造，也降低了维护面。

## 候选方向评分

评分为 1–10，竞争分越高表示竞争越可控、细分更清晰。不是市场规模预测，而是基于上述公开证据、现有项目边界和 60 秒 MVP 可验证性的孵化排序。

| 候选 | Demand | Competition | Differentiation | Technical depth | Build feasibility | OSS usefulness | Growth potential | 合计 / 70 | 结论 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **Artifact Fence：CI 制品上传范围证明** | 8 | 8 | 9 | 7 | 9 | 9 | 8 | **58** | 选择。扫描实际上传集合、脱敏报告、CI gate。 |
| MCP 会话录制/回放合同测试 | 8 | 4 | 5 | 8 | 6 | 8 | 8 | 47 | 有真实需求，但同名和相近 replay 项目已出现；不应换皮。 |
| MCP 配置锁文件/TBOM | 8 | 5 | 6 | 8 | 6 | 8 | 8 | 49 | 需求强，但 mcptrust 已覆盖运行时强制、签名、漂移；静态子集不足以单独胜出。 |
| CI 失败诊断归档代理 | 7 | 5 | 6 | 8 | 5 | 8 | 7 | 46 | 容易与 runner、日志平台和 PatchWitness 的证据理念重叠，MVP 维护面过大。 |
| Agent tool 权限策略编译器 | 8 | 6 | 4 | 7 | 8 | 8 | 7 | 48 | 可以合理并入 GuardSpec，淘汰。 |

## 最终选择：Artifact Fence

Artifact Fence 是一个**只读、静态的 GitHub Actions 制品暴露面分析器**。它解析 `actions/upload-artifact` 与 `actions/upload-pages-artifact`，在当前工作树中展开目录、通配符与排除模式；为避免 action ref 或运行时表达式导致 hidden-file 漏报，只有显式静态的 `include-hidden-files: false` 才排除隐藏文件，其他情形均按潜在上传枚举。它记录动态路径、拒绝跨平台越界路径与符号链接逃逸，并对拟上传文件执行不回显密钥值的轻量检查。`scan` 产出稳定 JSON；`check` 则按严重度以非零退出码成为 CI gate。

这个切口足够小：开发者只要看到 `build/**` 实际包含 `build/.env`，就能在 60 秒内理解价值。它不替代 TruffleHog 或 repo-privacy-guard；前者涵盖广泛 secrets 检测，后者检查准备公开的仓库内容，而 Artifact Fence 回答的是**一个更窄、可操作、与 CI 上传语义绑定的问题：这一上传步骤会带走什么？**

## 现有项目边界判定

| 项目 | 是否属于其 feature？ | 判定理由 |
|---|---|---|
| PatchWitness | **否** | PatchWitness 在变更已产生后校验范围、受保护路径、实跑检查与 Change Passport。它没有解析 Actions 的 upload 语义、展开制品路径或为 artifact 目录提供 exposure report。将其塞入会把变更信任门扩展成通用制品安全扫描器。 |
| GuardSpec | **否** | GuardSpec 在工作开始前把 `AGENTS.md` 等规则编译为路径、命令、网络、MCP 的 preflight policy；它不运行 CI workflow 静态解释，也不扫描真实输出文件。Artifact Fence 的输入是 workflow upload step 与工作树，输出是制品清单与风险。 |
| TaskToPR | **否** | TaskToPR 以一个 GitHub Issue 为单位创建隔离分支、运行测试并可开 PR。Artifact Fence 不调用模型、不创建分支、不执行任务或发 PR；它可作为任何 CI 或 PR 的独立本地门禁。 |
| repo-privacy-guard | **否，但互补** | repo-privacy-guard 扫描准备公开的仓库（含 staged mode）。Artifact Fence 不判断全仓库能否公开，专注上传 action 的选择范围，并可在每次 CI 上阻止风险制品。 |

## 设计边界与限制

MVP 只支持 GitHub Actions 中两个官方 upload action 的静态 `with.path`；`upload-pages-artifact` 省略 path 时使用 `_site/` 默认目录。它不会执行 workflow、shell、JavaScript、Docker、表达式或下载内容。包含 `${{ ... }}`、`$()` 或 `${...}` 的路径被标为动态而不会猜测展开结果。动态 CI 生成文件只在工作树当前存在时才能被列入扫描；因此报告是一个**当前可复现实例**，不是对任意远程 runner 运行时的完备证明。为控制资源消耗，workflow YAML 与每个制品内容检查均限制在 1 MiB。秘密启发式会有 false positive 与 false negative，且为避免泄露永不输出匹配值。

## References

[1]: https://github.com/Aider-AI/aider/issues/5552 "Aider #5552"
[2]: https://github.com/Aider-AI/aider/issues/5529 "Aider #5529"
[3]: https://github.com/openai/codex/issues/38689 "Codex #38689"
[4]: https://github.com/openai/codex/issues/38671 "Codex #38671"
[5]: https://github.com/modelcontextprotocol/typescript-sdk/issues/2628 "MCP TypeScript SDK #2628"
[6]: https://github.com/modelcontextprotocol/typescript-sdk/issues/2612 "MCP TypeScript SDK #2612"
[7]: https://github.com/modelcontextprotocol/typescript-sdk/issues/2627 "MCP TypeScript SDK #2627"
[8]: https://github.com/PrefectHQ/fastmcp/issues/4734 "FastMCP #4734"
[9]: https://github.com/PrefectHQ/fastmcp/issues/4747 "FastMCP #4747"
[10]: https://github.com/PrefectHQ/fastmcp/issues/4825 "FastMCP #4825"
[11]: https://github.com/microsoft/playwright/issues/42256 "Playwright #42256"
[12]: https://github.com/microsoft/playwright/issues/42188 "Playwright #42188"
[13]: https://github.com/actions/runner/issues/4632 "actions/runner #4632"
[14]: https://github.com/actions/runner/issues/4617 "actions/runner #4617"
[15]: https://github.com/reviewdog/reviewdog/issues/2150 "reviewdog #2150"
[16]: https://github.com/reviewdog/reviewdog/issues/2480 "reviewdog #2480"
[17]: https://github.com/ossf/scorecard/issues/5168 "OpenSSF Scorecard #5168"
[18]: https://github.com/ossf/scorecard/issues/4982 "OpenSSF Scorecard #4982"
[19]: https://github.com/renovatebot/renovate/issues/45236 "Renovate #45236"
[20]: https://github.com/trufflesecurity/trufflehog/issues/5205 "TruffleHog #5205"
[21]: https://github.com/trufflesecurity/trufflehog/issues/5190 "TruffleHog #5190"
[22]: https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/2189 "MCP Tool Bill of Materials discussion"
[23]: https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/2493 "Portable execution record discussion"
[24]: https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/2036 "MCP tool bloat discussion"
[25]: https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/2404 "Agent identity and delegation discussion"
[26]: https://github.com/mcptrust/mcptrust "mcptrust runtime security proxy"
