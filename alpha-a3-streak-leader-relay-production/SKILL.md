---
name: alpha-A3-production
description: 当需要读取「连板龙头接力」(A3) 因子的生产计算结果时，使用此 skill。该 skill 只读取已生成的 Parquet 结果，不在调用时重新计算因子。
tags: [quant, alpha, production, stock]
---

# 连板龙头接力 (A3) · 生产结果

## 适用场景
- 当用户需要查询 A3 最新交易日的接力信号时
- 当交易 agent 需要使用 A3 信号辅助 T+1 接力交易判断时
- 当用户需要查询历史 A3 信号回放时

## 结果文件
- 文件路径：`数据库.parquet`
- 数据格式：Parquet
- 更新频率：每日收盘后（建议 16:00 后）
- 生成方式：`alpha-A3/开发产物/scripts/factor.py` 定时计算

## 主键
- `trade_date`（= signal_date，信号形成日 t）
- `factor_id`（= `A3`）
- `ts_code`

## 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| trade_date | date | 信号形成日 t（与 signal_date 同义） |
| signal_date | date | 信号形成日 t |
| trade_exec_date | date | 实际入场日 t+1 |
| asset_type | string | 固定 `stock` |
| ts_code | string | 股票代码（如 `000001.SZ`） |
| name | string | 股票名称 |
| factor_id | string | 固定 `A3` |
| factor_name | string | 固定 `连板龙头接力` |
| factor_value | float | base_score 原值（z-score 加权和） |
| score | float | sigmoid(base_score) × 100 ∈ [0, 100]（**绝对评分**，跨日可比） |
| rank | int | 当日候选池排名（1 最强，每日只保留 rank ≤ 3） |
| signal | string | `buy`(score≥70) / `watch`(score≥50) / `hold` / `unfillable`(t+1 一字板) |
| confidence | float | 0-1 置信度 |
| limit_up_streak | int | 当日连板数 |
| streak_band_score | float | 板高横截面 z-score |
| concept_leader_score | float | 题材内龙头身份（0-1） |
| concept_followers_rate | float | 题材跟封率（log 归一化 0-1，衡量梯队厚度） |
| early_seal_score | float | 早封板得分（0-1，越大越早封） |
| seal_strength_proxy | float | 封板强度代理（`-turnover`） |
| blow_up_proxy | float | 炸板风险（0-1，越大越易炸） |
| position_60d | float | 60 日相对位置（0-1） |
| lhb_hotmoney | float | 龙虎榜游资净买入（元） |
| is_one_word | bool | 是否一字板（t+1 不可成交参考） |
| forward_return | float | T+1 open → T+2 vwap 毛收益（自带缓存，回测不重拉行情） |
| fillable | bool | T+1 可成交（非一字、非开盘涨停、非停牌） |
| next_open | float | T+1 开盘价（诊断） |
| vwap_t2 | float | T+2 加权均价（诊断） |
| data_version | string | 数据版本 `pandadata-relay-streak3-v2` |
| update_time | datetime | 结果生成时间 |

## 读取规则

```python
import pandas as pd

df = pd.read_parquet("alpha-A3/生产产物/数据库.parquet")

# 取最新交易日所有 buy 信号
latest = df["trade_date"].max()
buys = df[(df["trade_date"] == latest) & (df["signal"] == "buy")]
buys = buys.sort_values("rank")   # rank = 1 最强

# 字段示例
for _, r in buys.iterrows():
    print(f"{r['ts_code']} {r['name']} "
          f"streak={r['limit_up_streak']} "
          f"score={r['score']:.0f} "
          f"confidence={r['confidence']:.2f} "
          f"→ {r['signal_date'].date()} 收盘信号、"
          f"{r['trade_exec_date'].date()} 入场")
```

## 读取规则
- 默认使用最新有效交易日结果。
- 最新结果缺失时可回退最近有效交易日，但必须说明数据日期。
- `signal == 'unfillable'`：T+1 一字板 / 开盘涨停 / 停牌，**不可成交**，不应下单。
- 每日**只取 rank=1 的 1 只**或前几只（事件型信号，不是横截面 top-N）。

## 持有规则（A 股 T+1 制度）
- T 日 15:00 收盘后形成信号
- T+1 开盘买入（不是一字板的情况下）
- T+1 当天**不能卖**（T+1 制度）
- T+2 全日 vwap（amount/volume）卖出
- 双成本门槛：标准 0.30% / 压力 0.50%

## 禁止行为
- 不允许在 agent 调用时重新拉取原始行情
- 不允许在 agent 调用时重新计算因子
- 不允许手工修改 Parquet 结果
