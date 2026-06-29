# skill-alpha-a3-streak-leader-relay

[简体中文](README.md) | [English](README.en.md)

> 从全 A 市场每日 ≥3 板候选中识别 T+1 接力的事件型 top-N 信号 — 候选发现器，非交易策略。

## ⚠️ 免责声明

本仓库是 **仅供研究与教育的开源 skill 示例**，**不构成投资建议、收益承诺、官方背书或可生产部署的策略**。

- 所有回测数据描述的是模型在**历史样本闭合区间内**的统计表现，不代表未来表现
- 任何"年化"、"累计收益"等数字都是基于历史回测窗口外推的模型行为，**不是真实交易盈亏**
- 因子高度依赖游资活跃环境，环境一变会失效
- 实盘运行会遇到滑点、部分成交、停牌、跌停无法卖出、流动性枯竭等回测无法完全模拟的风险
- 使用本项目所发生的任何决策与损失，由使用者自行承担

## 🎯 因子定位

A3 是一个**事件型 T+1 接力因子的 skill 框架**：

1. 从全 A 市场每日 ≥3 板的股票中**筛选候选池**
2. 用 **10 个子因子**（个股截面 8 + 大盘情绪 2）打分
3. 输出**每日 top-3 候选**与 `buy` / `watch` / `hold` / `unfillable` 信号分级

它是一个**研究层面的候选发现器**，不是黑盒交易策略。下游的择时、执行与风控规则由使用者自行决定。

## 📊 历史样本表现（基于 2023-06 ~ 2026-06 闭合回测窗口）

> 以下数据描述模型在历史窗口内的**回测统计行为**，不代表真实交易结果，也不代表未来可重现：

- 2023-06 ~ 2026-06 共 722 个交易日、1978 条 ≥3 板候选信号
- 近 6 个月样本外窗口（OOS）：
  - 因子横截面预测力：IC ≈ 0.107、ICIR ≈ 0.137
  - 含 IC gate + 大盘情绪 + score 加权组合在 0.10% 双边成本（万五佣金 + 印花税）假设下，回测窗口内**模型累计行为** ≈ +33.8%；最大回撤约 -33%
  - 在 0.30% 假设成本下回测累计 ≈ +22.8%
- 训练样本内（IS）IC ≈ 0.008，**与 OOS 显著分化** — 因子对游资活跃环境高度敏感

**关键约束**：
- 不能裸用纯 A3 — 必须配合 IC gate + 大盘情绪过滤
- 成本敏感 — 实盘双边费率应控制在 ≤ 0.15%
- 子因子权重可用 `calibrate_weights.py`（ICIR + shrinkage）重训，**但激进校准容易过拟合**，默认权重已经过经验调校

## 🔧 可训练能力

A3 是个**可调因子 skill**，不是黑盒：

| 维度 | 工具 / 参数 | 用法 |
|---|---|---|
| 子因子权重 | `calibrate_weights.py` | ICIR + shrinkage 校准，输出 JSON 供 factor.py 加载 |
| 大盘情绪窗口 | `DEFAULT_CONFIG["mkt_state_window"]` | 60 日 z-score（可调） |
| 仓位加权方式 | `strategy_backtest.py --weighting` | `equal` / `score`（默认 score） |
| 校准训练区间 | `calibrate_weights.py --train-end YYYYMMDD` | 默认留出 6 月 OOS |
| 收缩强度 | `calibrate_weights.py --shrinkage 0.5` | 0=纯ICIR / 1=纯先验 / 0.5=平衡 |
| 单权重上限 | `calibrate_weights.py --max-abs-weight 0.25` | 防过拟合 |

⚠️ **校准当前的实证结论**：8 个子因子 ICIR 中除 `position_60d` 外信号普遍弱，激进校准（shrinkage=0）在 OOS 会过拟合（纯 A3 -90%）。**默认权重已经过经验调校，不建议轻易覆盖**。

## 目录结构

```
skill-alpha-a3-streak-leader-relay/
├── README.md                              ← 本文件（交付说明）
├── skill-alpha-a3-streak-leader-relay/        ← 开发产物
│   ├── SKILL.md                           ← 因子设计书（开发版，按规则 V2 §5 模板）
│   ├── skill.json                         ← Skill 元数据
│   ├── streak_curve_calibrated.json       ← 板高 IS 拟合曲线（探索性产物）
│   ├── scripts/
│   │   ├── factor.py                      ← 因子主计算脚本（必须文件）
│   │   ├── validate.py                    ← 三层沙漏验证（必须文件）
│   │   ├── backtest.py                    ← 因子回测（必须文件，IC/IR/分层/换手）
│   │   ├── strategy_backtest.py           ← 策略层回测（top-N 仓位 + IC gate 对比）
│   │   ├── render_html.py                 ← HTML 可视化报告生成器
│   │   ├── calibrate_weights.py           ← 子因子权重 ICIR 校准（可训练能力）
│   │   └── calibrate_streak.py            ← 板高曲线 IS 拟合工具（探索性）
│   └── references/
│       └── data_guide.md                  ← 数据指南（必须文件）
└── skill-alpha-a3-streak-leader-relay-production/   ← 生产产物
    ├── SKILL.md                           ← 生产读取说明（规则 V2 §8 模板）
    ├── database.parquet                   ← 因子计算结果（生产读取）
    ├── weights_calibrated.json            ← 子因子校准权重（可选；不存在则用默认）
    ├── backtest_report.md / backtest_metrics.json          ← 因子回测交付材料
    ├── strategy_backtest_report.md / strategy_backtest_metrics.json  ← 策略层回测交付材料
    └── backtest_report.html                      ← 可视化报告（资金曲线 / IC 时序 / Q1-Q5 柱）
```

## 快速开始

```bash
# 1. 配置凭证（也可写入 ~/.pandadata/pandadata.env）
export PANDA_USERNAME=<86 手机号>
export PANDA_PASSWORD=<密码>

# 2. 跑全套
cd skill-alpha-a3-streak-leader-relay/scripts

# 2.1 计算因子（3 年全市场，~15 分钟）
python factor.py --start 20230619 --end 20260619 \
    --out ../../skill-alpha-a3-streak-leader-relay-production/database.parquet

# 2.1.5 [可选] 重训子因子权重（ICIR + shrinkage 校准）
#   - 不跑就用 DEFAULT_CONFIG 默认权重（已经过经验调校）
#   - 想试激进版可加 --shrinkage 0.3，但容易过拟合
python calibrate_weights.py
#   再跑 factor.py 会自动加载 weights_calibrated.json

# 2.2 三层沙漏验证
python validate.py

# 2.3 因子回测（指标 + 时序数据）
python backtest.py --start 20230619 --end 20260619 \
    --factor-parquet ../../skill-alpha-a3-streak-leader-relay-production/database.parquet \
    --out ../../skill-alpha-a3-streak-leader-relay-production/backtest_report.md \
    --out-json ../../skill-alpha-a3-streak-leader-relay-production/backtest_metrics.json

# 2.4 策略层回测（top-N + 双成本 + IC gate 对比）
python strategy_backtest.py --start 20230619 --end 20260619 \
    --factor-parquet ../../skill-alpha-a3-streak-leader-relay-production/database.parquet --top-n 3 \
    --out ../../skill-alpha-a3-streak-leader-relay-production/strategy_backtest_report.md \
    --out-json ../../skill-alpha-a3-streak-leader-relay-production/strategy_backtest_metrics.json

# 2.5 HTML 可视化
python render_html.py \
    --factor-parquet ../../skill-alpha-a3-streak-leader-relay-production/database.parquet \
    --factor-report ../../skill-alpha-a3-streak-leader-relay-production/backtest_metrics.json \
    --strategy-report ../../skill-alpha-a3-streak-leader-relay-production/strategy_backtest_metrics.json \
    --out ../../skill-alpha-a3-streak-leader-relay-production/backtest_report.html
```

## 核心设计要点

1. **事件型因子**：每日只输出 top ≤ 3 信号（不是横截面打分全样本）
2. **绝对评分**：score 是 sigmoid(base_score) × 100，跨日可比（不是横截面百分位）
3. **A 股 T+1 制度**：T 日收盘后形成信号 → T+1 open 进 → T+2 vwap 出
4. **forward_return 缓存**：parquet 自带，回测不重拉行情（省流量）
5. **无 Layer 2 状态机**：v1/v2 状态机经回测证明有害（让胜率下降），删除；策略层用滚动 60 日 IC gate 替代
6. **大盘情绪 + IC gate 协同**：mkt_state 标量子因子（当日全市场涨停家数 / 炸板率 60 日 z-score）+ 滚动 IC gate；ablation 显示两者组合显著改善 OOS 模型行为
7. **仓位 score 加权**：score 越高仓位越大（`--weighting score`）

## 10 个子因子（个股截面 8 + 大盘情绪 2）

| 子项 | 权重 | 数据来源 |
|---|---:|---|
| **个股截面（横截面 z-score 加权）** | | |
| limit_up_streak（连板数） | +0.18 | get_stock_daily |
| concept_leader_score（题材龙头） | +0.18 | get_concept_constituents |
| early_seal_score（早封板得分） | +0.13 | get_stock_min 或日线近似 |
| seal_strength_proxy（封板强度） | +0.09 | get_factor（turnover） |
| blow_up_proxy（炸板风险） | -0.13 | get_stock_min 或日线近似 |
| position_60d（60 日位置） | -0.09 | get_stock_daily |
| lhb_hotmoney（游资共振） | +0.05 | get_lhb_detail |
| concept_followers_rate（题材跟封率） | +0.05 | get_concept_constituents |
| **大盘情绪（标量，跨日 z-score）** | | |
| mkt_lu_z（全市场涨停家数 60d z） | +0.10 | 全 A daily 累加 |
| mkt_blow_up_z（全市场炸板率 60d z） | -0.10 | 全 A daily 累加 |

## 已知关键发现（实证）

| 发现 | 数据 | 含义 |
|---|---|---|
| 候选池次日均值负偏 | 2-7 板候选 avg_fwd_return ∈ [-4.31%, -1.18%] | A 股 ≥3 板候选**次日整体均值是回归的** |
| IS / OOS 分化巨大 | IS IC=0.054 / OOS IC=0.179 | 因子有效但**严重依赖游资活跃环境** |
| 一字板占比 30% | unfillable 1335 / 4324 = 31% | 高度龙头**真实抢不到的比例** |
| Q5-Q1 分层显著正 | ALL +1.50% / OOS +3.78% | 因子**有区分力**，但分组有效 ≠ 多头赚钱 |

## 验收状态

| 项 | 状态 |
|---|---|
| `factor.py` 独立可运行 | ✅ |
| `validate.py` 三层沙漏（合成数据） | ✅ 44/44 PASS |
| `backtest.py` 输出 IC/RankIC/ICIR/IR/CR/ARR/MDD/分层/换手 | ✅ |
| 数据源 panda_data | ✅ |
| 生产 parquet 主键唯一 | ✅ |
| 生产版 SKILL.md | ✅ |
| 必须字段非空率 | ✅ 100% |
| score 范围 0-100 | ✅ |
| signal 枚举合法 | ✅ (buy/watch/hold/unfillable) |

## 局限与后续优化方向

| 局限 | 说明 | 后续 |
|---|---|---|
| 候选池整体负偏 | A 股近 3 年 ≥3 板次日整体回归 | 待"游资活跃环境"识别器（IC gate 已是初版） |
| early_seal 当前用日线近似 | 流量优化已就绪 | 流量配额充足时跑 `--with-minute` |
| 概念 PIT 用快照 | 已过滤 `in_date ≤ signal_date` | 后续改按交易日逐日拉 PIT 成分 |
| 板高 IS 拟合曲线噪音大 | 7-13 板样本不足 | 累计 1 年数据后重新校准 |
| Layer 3 早盘事件触发未实现 | 用户最初设想"换手前五首封" | 实时数据接入后做 |

## 维护

- 数据版本：`pandadata-relay-streak3-v2`
- 生成方式：每日 16:00 收盘后跑 `factor.py`
- 建议增量更新：每日只跑新一天，参数 `--start <昨天> --end <今天>`
