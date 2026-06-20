# Alpha-A3 · 连板龙头接力因子

> 从全 A 市场每日 ≥3 板候选中识别最值得 T+1 接力的龙头标的（top-N 事件型信号）。

## 目录结构

```
alpha-a3-streak-leader-relay/
├── README.md                              ← 本文件（交付说明）
├── alpha-a3-streak-leader-relay/        ← 开发产物
│   ├── SKILL.md                           ← 因子设计书（开发版，按规则 V2 §5 模板）
│   ├── skill.json                         ← Skill 元数据
│   ├── streak_curve_calibrated.json       ← 板高 IS 拟合曲线（探索性产物）
│   ├── scripts/
│   │   ├── factor.py                      ← 因子主计算脚本（必须文件）
│   │   ├── validate.py                    ← 三层沙漏验证（必须文件）
│   │   ├── backtest.py                    ← 因子回测（必须文件，IC/IR/分层/换手）
│   │   ├── strategy_backtest.py           ← 策略层回测（top-N 仓位 + IC gate 对比）
│   │   ├── render_html.py                 ← HTML 可视化报告生成器
│   │   └── calibrate_streak.py            ← 板高曲线 IS 拟合工具（探索性）
│   └── references/
│       └── data_guide.md                  ← 数据指南（必须文件）
└── alpha-a3-streak-leader-relay-production/   ← 生产产物
    ├── SKILL.md                           ← 生产读取说明（规则 V2 §8 模板）
    ├── database.parquet                   ← 因子计算结果（生产读取）
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
cd alpha-a3-streak-leader-relay/scripts

# 2.1 计算因子（3 年全市场，~15 分钟）
python factor.py --start 20230619 --end 20260619 \
    --out ../../alpha-a3-streak-leader-relay-production/database.parquet

# 2.2 三层沙漏验证
python validate.py

# 2.3 因子回测（指标 + 时序数据）
python backtest.py --start 20230619 --end 20260619 \
    --factor-parquet ../../alpha-a3-streak-leader-relay-production/database.parquet \
    --out ../../alpha-a3-streak-leader-relay-production/backtest_report.md \
    --out-json ../../alpha-a3-streak-leader-relay-production/backtest_metrics.json

# 2.4 策略层回测（top-N + 双成本 + IC gate 对比）
python strategy_backtest.py --start 20230619 --end 20260619 \
    --factor-parquet ../../alpha-a3-streak-leader-relay-production/database.parquet --top-n 3 \
    --out ../../alpha-a3-streak-leader-relay-production/strategy_backtest_report.md \
    --out-json ../../alpha-a3-streak-leader-relay-production/strategy_backtest_metrics.json

# 2.5 HTML 可视化
python render_html.py \
    --factor-parquet ../../alpha-a3-streak-leader-relay-production/database.parquet \
    --factor-report ../../alpha-a3-streak-leader-relay-production/backtest_metrics.json \
    --strategy-report ../../alpha-a3-streak-leader-relay-production/strategy_backtest_metrics.json \
    --out ../../alpha-a3-streak-leader-relay-production/backtest_report.html
```

## 核心设计要点

1. **事件型因子**：每日只输出 top ≤ 3 信号（不是横截面打分全样本）
2. **绝对评分**：score 是 sigmoid(base_score) × 100，跨日可比（不是横截面百分位）
3. **A 股 T+1 制度**：T 日收盘后形成信号 → T+1 open 进 → T+2 vwap 出
4. **forward_return 缓存**：parquet 自带，回测不重拉行情（省流量）
5. **无 Layer 2 状态机**：v1/v2 状态机经回测证明有害（让胜率下降），删除；策略层用滚动 60 日 IC gate 替代

## 7 个子因子（横截面 z-score 加权）

| 子项 | 权重 | 数据来源 |
|---|---:|---|
| limit_up_streak（连板数） | +0.20 | get_stock_daily |
| concept_leader_score（题材龙头） | +0.20 | get_concept_constituents |
| early_seal_score（早封板得分） | +0.15 | get_stock_min（`--with-minute`） 或日线近似 |
| seal_strength_proxy（封板强度） | +0.10 | get_factor（turnover） |
| blow_up_proxy（炸板风险） | -0.15 | get_stock_min 或日线近似 |
| position_60d（60 日位置） | -0.10 | get_stock_daily |
| lhb_hotmoney（游资共振） | +0.05 | get_lhb_detail |
| concept_followers_rate（题材跟封率） | +0.05 | get_concept_constituents |

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
