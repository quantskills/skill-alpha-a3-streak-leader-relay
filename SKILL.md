---
name: skill-alpha-a3-streak-leader-relay
description: 连板龙头接力（A3）Alpha 因子——从全 A 市场每日 ≥3 板候选池中识别 T+1 接力的事件型 top-N 信号，10 个子因子（个股截面 8 + 大盘情绪 2），权重可用 ICIR + shrinkage 重训，含滚动 IC gate 与 score 加权。研究层面的候选发现器，非交易策略。
tags: [a-share, alpha-factor, streak-leader, limit-up, event-driven, pandadata]
license: GPL-3.0-only
metadata:
  organization: QuantSkills
  organization_url: https://github.com/quantskills
  repository: skill-alpha-a3-streak-leader-relay
  repository_url: https://github.com/quantskills/skill-alpha-a3-streak-leader-relay
  project_type: skill
  collection: alpha-factor
  license: GPL-3.0-only
  category: factor
  status: community-project
---

# 连板龙头接力 Alpha（A3）

> **项目状态：Community Project（社区项目）。** 本项目由社区成员创建，**未经 QuantSkills 官方审核、认证、验证或背书**，
> 也非生产可用认证项目。名称中的 `quantskills/` 仅表示托管组织，不代表任何官方身份。

> **一句话**：从全 A 市场每日 **≥3 板**候选中识别 T+1 接力的事件型 top-N 信号。
> 它是**研究层面的候选发现器**，不是交易策略——下游的择时、执行与风控规则由使用者自行决定。

## 这个项目做什么

1. 从全 A 市场每日 ≥3 板的股票中**筛选候选池**
2. 用 **10 个子因子**（个股截面 8 + 大盘情绪 2）打分
3. 输出**每日 top-3 候选**与 `buy` / `watch` / `hold` / `unfillable` 信号分级

## 目录

| 路径 | 内容 |
|---|---|
| [`skill-alpha-a3-streak-leader-relay/`](skill-alpha-a3-streak-leader-relay/) | 开发产物：因子计算、权重校准、回测、校验脚本 + 详细 [SKILL.md](skill-alpha-a3-streak-leader-relay/SKILL.md) |
| [`skill-alpha-a3-streak-leader-relay-production/`](skill-alpha-a3-streak-leader-relay-production/) | 生产产物：`database.parquet` 信号面板、回测报告 + 读取规则 [SKILL.md](skill-alpha-a3-streak-leader-relay-production/SKILL.md) |
| [README.md](README.md) / [README.en.md](README.en.md) | 中 / 英文交付说明（含完整回测口径与风险提示） |

## 怎么用

```bash
pip install --upgrade panda_data pyarrow
export PANDA_USERNAME=<手机号>; export PANDA_PASSWORD=<密码>   # 或 ~/.pandadata/pandadata.env

python skill-alpha-a3-streak-leader-relay/scripts/factor.py             # 计算因子与每日候选
python skill-alpha-a3-streak-leader-relay/scripts/calibrate_weights.py  # ICIR + shrinkage 重训子因子权重
python skill-alpha-a3-streak-leader-relay/scripts/backtest.py           # IC / IR / 分层 / 换手 / 资金曲线
python skill-alpha-a3-streak-leader-relay/scripts/strategy_backtest.py  # 含 IC gate + 情绪过滤的组合回测
python skill-alpha-a3-streak-leader-relay/scripts/validate.py           # 交付校验
```

完整调用规则、子因子定义与字段表见 **[开发 SKILL.md](skill-alpha-a3-streak-leader-relay/SKILL.md)**。

## 数据来源、假设与参数

- **数据来源**：PandaData（`panda_data >= 0.0.9`）行情 + 概念 + 龙虎榜。
  凭证走环境变量 `PANDA_USERNAME` / `PANDA_PASSWORD` 或 `~/.pandadata/pandadata.env`，**绝不硬编码**。
- **假设**：≥3 板的高度龙头具备 T+1 接力的截面可区分性；子因子权重可由历史 ICIR 估计并做 shrinkage。
- **参数**：候选门槛（≥3 板）、top-N（默认 3）、滚动 IC gate 窗口、大盘情绪 z-score 阈值、
  假设双边成本；均可在脚本参数中调整，口径见开发 SKILL.md。

## 已知限制与风险边界

- **因子高度依赖游资活跃环境**，环境一变会失效；训练样本内（IS）IC ≈ 0.008 与
  样本外（OOS）IC ≈ 0.107 **显著分化**。
- **不能裸用**——必须配合 IC gate + 大盘情绪过滤。
- **成本敏感**——实盘双边费率应控制在 ≤ 0.15%；0.30% 假设成本下回测收益明显收窄。
- 回测数字描述的是模型在**历史闭合窗口内的统计行为**，不代表未来表现，也不是真实交易盈亏。
- 实盘会遇到滑点、部分成交、停牌、跌停无法卖出、流动性枯竭等回测无法完全模拟的风险。

> **Community Project，未经 QuantSkills 官方审核 / 认证 / 背书。仅供研究与教育示例，
> 不构成投资建议，不承诺收益。** 使用本项目所发生的任何决策与损失，由使用者自行承担。

License: **GPL-3.0-only**
