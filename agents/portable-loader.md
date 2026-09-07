# Portable Loader Prompt / 便携加载提示

This file is the portable entrypoint for Hermes, OpenClaw, and any agent runtime that does not
natively discover `SKILL.md` folders. Native runtimes (Claude Code, Codex, Cursor) should read
[SKILL.md](../SKILL.md) directly; the Cursor rule is [cursor-rule.mdc](cursor-rule.mdc) and the
Codex interface is [openai.yaml](openai.yaml).

本文件是 Hermes / OpenClaw 等不原生识别 `SKILL.md` 目录的运行时的便携入口。
把下面这段提示粘贴进运行时的系统提示或工具描述，并把占位路径替换成仓库根目录。

```text
You have access to a local skill named skill-alpha-a3-streak-leader-relay at:
<SKILL_ALPHA_A3_STREAK_LEADER_RELAY_ROOT>

Use it when the user wants to compute, calibrate, or backtest the A3 streak-leader relay alpha: pick T+1 relay top-N signals from daily ≥3-board candidates, retrain sub-factor weights, or inspect IC / quantile / equity-curve diagnostics.

1. Read <SKILL_ALPHA_A3_STREAK_LEADER_RELAY_ROOT>/SKILL.md first; it is the canonical declaration.
2. Read <SKILL_ALPHA_A3_STREAK_LEADER_RELAY_ROOT>/skill-alpha-a3-streak-leader-relay/SKILL.md for the agent-facing interface (inputs, outputs, run()/validate_input()).
3. Read the references before touching data logic:
   - <SKILL_ALPHA_A3_STREAK_LEADER_RELAY_ROOT>/skill-alpha-a3-streak-leader-relay-production/SKILL.md
4. Run entrypoints from <SKILL_ALPHA_A3_STREAK_LEADER_RELAY_ROOT>:
   python skill-alpha-a3-streak-leader-relay/scripts/factor.py
   python skill-alpha-a3-streak-leader-relay/scripts/calibrate_weights.py
   python skill-alpha-a3-streak-leader-relay/scripts/strategy_backtest.py
   python skill-alpha-a3-streak-leader-relay/scripts/validate.py
5. This is a research-level candidate finder, not a trading strategy; never use it bare, always with the rolling IC gate and market-sentiment filter
6. Backtest figures are model statistics inside a closed historical window, not real P&L; never rewrite them as return promises
7. The factor depends on an active hot-money regime and in-sample vs out-of-sample IC diverge sharply; the output must relay these known limits
8. Credentials come only from PANDA_USERNAME / PANDA_PASSWORD or ~/.pandadata/pandadata.env; never hard-code or print them.
9. This is a QuantSkills Community Project for research and education only: no investment advice, no return promises, no claim of official endorsement.
```
