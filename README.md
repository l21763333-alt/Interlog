<div align="center">

# Iterlog

### 把零散的修改、优化与验证，沉淀为可继续推进的开发闭环。

面向 Codex 与 Claude Code 的证据驱动开发复盘 Skill/Subagent。<br>
一次调用，整理成果、问题、具体变更、验证、改进建议、反思与下一步。

<code>Codex</code> · <code>Claude Code</code> · <code>Read-only</code> · <code>Local-first</code> · <code>MIT</code>

[English](README.en.md) · [架构](docs/architecture.md) · [隐私](docs/privacy.md) · [故障排查](docs/troubleshooting.md)

</div>

~~~text
$iterlog 复盘今天的开发工作
~~~

~~~text
/iterlog:iterlog 复盘今天的开发工作
~~~

> 修改 → 优化 → 验证 → 复盘 → 继续。无需重新拼接上下文，也无需反复指定报告格式。

## Iterlog 能做什么

- 归纳已经完成的修改、结果与影响
- 整理进行中的优化、剩余工作和下一步
- 记录问题、原因、影响与解除条件
- 汇总关键文件或组件的具体变更
- 区分验证通过、失败、未执行和未知
- 给出有证据支撑的改进建议与反思
- 为结论附上 commit、diff、文件、检查记录或会话证据

每次调用都会创建一份新的 Markdown 复盘。报告保存在项目目录之外，负责总结的 Subagent 不会修改业务代码。

<details>
<summary>查看输出预览</summary>

~~~text
覆盖状态：partial
完成：修复登录状态过期后的重复跳转
验证：登录与会话刷新流程检查通过
风险：一个短会话没有触发自动 compact
建议：为会话失效增加可观测指标
反思：先复现再缩小修改范围，减少了无关变更
下一步：补充异常网络条件下的恢复检查
~~~

</details>

## 为什么是 Iterlog

**Iterlog = Iteration + Log。** 它记录的不是一句模糊的“今天做了什么”，而是一轮可以继续推进的开发迭代：

~~~text
你的开发：修改 ──→ 优化 ──→ 验证
Iterlog： 捕获 ──→ 校验 ──→ 复盘 ──→ 下一步
~~~

长会话可能被自动 compact，短会话又可能没有完整历史。Iterlog 会在自动 compact 前保留本地证据，并在显式调用时由一个只读 Subagent 核对证据、去重并生成报告。证据不足时会标记为 <code>partial</code> 或 <code>empty</code>，不会把未知内容包装成确定结论。

## 快速开始

### 环境要求

- 支持 Plugin Marketplace 与 Hooks 的 Codex 或 Claude Code
- Python 3.10+
- Claude Code 跨平台启动器需要 Node.js

Marketplace 安装不运行 Python 安装脚本。Python 仅作为本地运行时，用于证据捕获、校验、脱敏和 Markdown 渲染。

### Codex

~~~bash
codex plugin marketplace add l21763333-alt/Interlog
codex plugin add iterlog@iterlog-marketplace
~~~

打开新的 Codex 任务后调用：

~~~text
$iterlog 复盘今天的开发工作
~~~

### Claude Code

~~~text
/plugin marketplace add l21763333-alt/Interlog
/plugin install iterlog@iterlog-marketplace
/reload-plugins
~~~

调用内置 Skill：

~~~text
/iterlog:iterlog 复盘今天的开发工作
~~~

也可以从 Shell 安装：

~~~bash
claude plugin marketplace add l21763333-alt/Interlog
claude plugin install iterlog@iterlog-marketplace --scope user
~~~

## 使用示例

无需重复说明章节、证据规则、保存路径或只读要求。直接指定日期或关注范围即可：

~~~text
$iterlog 复盘今天的开发工作
$iterlog 总结本次登录问题的原因、修改、验证与反思
$iterlog 复盘 <YYYY-MM-DD> 的工作，时区 Asia/Shanghai
$iterlog 只关注支付模块的修改、风险和下一步
~~~

Claude Code 使用相同的自然语言，只需把命令前缀换成 <code>/iterlog:iterlog</code>。也可以直接说：“使用 Iterlog 总结今天的修改和优化。”

## 报告结构

每份 Iterlog 固定包含九个部分：

1. 日期、范围与证据覆盖
2. 开发概览
3. 已完成修改与成果
4. 进行中的修改与优化
5. 阻塞与风险
6. 关键变更、决策与验证
7. 改进建议与下一步
8. 反思、证据缺口与待确认
9. 证据索引

默认范围是当前项目和运行环境的本地当天。可以指定日期、时区或更窄的模块范围，但不会静默跨项目汇总。

## 工作原理

~~~text
PreCompact(auto)
  └─ 保存本地 transcript 快照并记录完整性信息

$iterlog / /iterlog:iterlog
  └─ 启动一个只读 Subagent
     └─ 校验证据 → 去重归纳 → 生成结构化报告
        └─ 保存新的 Markdown Iterlog
~~~

需要注意：

- 仅在自动 compact 前捕获快照；手动 compact 不产生自动快照。
- 捕获不会自动生成报告，只有显式调用 Skill 才会复盘。
- 磁盘上的会话记录可能落后于界面中的最新消息，报告会如实列出覆盖缺口。
- 已完成事项、关键变更、建议与反思必须有证据；执行过命令不等于验证通过。

更完整的数据流见[架构文档](docs/architecture.md)。

## 数据与安全

Iterlog 采用本地优先设计，运行时不会主动访问网络，也不会把报告写入业务项目。

默认数据目录：

- Codex：<code>~/.codex/iterlog</code>
- Claude Code：<code>~/.claude/iterlog</code>

捕获的原始会话可能包含源码、工具输出、路径、凭据或个人信息。请勿提交或分享 Iterlog 数据目录。报告会执行常见敏感信息脱敏，但公开前仍应人工检查。

常用配置：

- <code>ITERLOG_HOME</code>：指定项目外的本地数据目录
- <code>ITERLOG_PYTHON</code>：指定 Python 3.10+ 解释器

完整的数据处理与配置说明见[隐私文档](docs/privacy.md)。

<details>
<summary>可选：安装命名 Codex Custom Agent</summary>

仅在确实需要固定名称 <code>iterlog</code> 的 Codex Custom Agent 时使用：

~~~bash
python3 scripts/manage.py install
python3 scripts/manage.py doctor
~~~

该模式是 Marketplace 安装的替代方案，请勿同时启用两套 Hook。

</details>

## 常见问题

### Iterlog 会修改代码吗？

不会。报告 Subagent 只读分析当前项目和已捕获证据。

### 每次都要指定报告格式吗？

不需要。章节、证据规则、只读边界和保存方式已经包含在 Skill 中。

### 没有发生自动 compact 怎么办？

仍可基于当前可见证据生成报告，但覆盖状态会标记为 <code>partial</code> 或 <code>empty</code>。

### 报告会自动生成吗？

不会。只有调用 <code>$iterlog</code> 或 <code>/iterlog:iterlog</code> 后才会生成报告。

## 文档

- [架构说明](docs/architecture.md)
- [隐私与数据处理](docs/privacy.md)
- [故障排查](docs/troubleshooting.md)
- [安全策略](SECURITY.md)
- [更新记录](CHANGELOG.md)

## License

[MIT](LICENSE)
