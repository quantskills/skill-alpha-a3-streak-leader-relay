---
name: alpha-A3
description: 当需要开发、计算、验证「连板龙头接力」因子时，使用此 skill。从全 A 市场每日 ≥3 板候选中识别最值得 T+1 接力的龙头标的（top-N 事件型信号）。
tags: [quant, alpha, development, stock, limit_up]
---

# 连板龙头接力 Alpha（A3）

## 适用场景
- 当用户需要每日识别 ≥3 板高度龙头并生成 T+1 接力 buy 信号时
- 当用户需要 T+1 当日完成接力交易（开盘买、次日 t+2 vwap 出）的信号时
- 当用户需要 IC / IR / 分层 / 换手 / 资金曲线等完整回测指标时

## 因子逻辑

- **核心假设**：A 股游资生态中，每日全市场 ≥3 板候选中**真正具备接力价值**的极少数标的（每日 top-1 ~ top-3），可以通过 7 个维度的横截面 z-score 加权识别。
- **事件型设计**：A3 是**事件型因子**（不是横截面打分取 top-N），每日只输出 1-3 只信号（rank ≤ max_signals_per_day）。
- **计算公式**：
  ```
  base_score =
    + 0.20·z(limit_up_streak)        连板高度（核心）
    + 0.20·z(concept_leader_score)   题材内龙头身份
    + 0.15·z(early_seal_score)       早封板得分（分钟线精确化，可选）
    + 0.10·z(seal_strength_proxy)    封板强度（-turnover 代理）
    - 0.15·z(blow_up_proxy)          炸板风险
    - 0.10·z(position_60d)           60 日相对位置
    + 0.05·z(lhb_hotmoney)           龙虎榜共振
    + 0.05·z(concept_followers_rate) 题材跟封率（梯队厚度）
  ```
- **score 计算**：base_score 经 sigmoid 映射到 0-100（绝对评分，跨日可比，不是横截面百分位）。
  ```
  score = 100 / (1 + exp(-2 * base_score))
  ```
- **signal 枚举**：
  - `buy`（score ≥ 70）
  - `watch`（score ≥ 50）
  - `hold`（其他）
  - `unfillable`（T+1 一字板）
- **信号收敛**：每日只保留 rank ≤ 3 的候选（剔除"矮子里挑高个"的伪信号）。
- **适用市场**：A 股全 A（主板 + 创业板），剔除 ST / 科创板 / 北交所。
- **持有周期**：T+1 当日（T 日收盘后形成信号 → T+1 open 进 → T+2 vwap 出，符合 A 股 T+1 制度）。

## 输入数据

因子计算使用 panda_data 数据拉取库：

| 接口 | 用途 | 频率 |
|---|---|---|
| `get_stock_daily` | 全市场日线（OHLC + limit_up + trade_status） | 日频 |
| `get_concept_constituents` | 概念成分股（按概念遍历拉避免 600003 限额） | 日频 |
| `get_lhb_detail` | 龙虎榜买方明细（分 31 天/段拉） | 披露日 |
| `get_factor` | market_cap / turnover（分 3 年/段拉，避 100008 限额） | 日频 |
| `get_stock_min` | 候选池涨停日 1m 分钟线（`--with-minute` 启用） | 日内 1m |

| 字段 | 说明 | 来源 |
|---|---|---|
| trade_date / ts_code | 主键 | 行情 |
| open / close / high / low / volume / amount | OHLCV | get_stock_daily |
| limit_up / pre_close / trade_status | 涨停判定 | get_stock_daily |
| concept / in_date | 概念成分 PIT 过滤 | get_concept_constituents |
| agency / b_value / s_value | 龙虎榜买方 | get_lhb_detail |
| market_cap / turnover | 流通市值 / 换手 | get_factor |

## 输出结果

| 字段 | 类型 | 说明 |
|---|---|---|
| trade_date | date | 信号日 t（= signal_date，规则 §9 标准主键） |
| signal_date | date | 同 trade_date |
| trade_exec_date | date | 实际入场日 t+1 |
| asset_type | string | `stock` |
| ts_code | string | 股票代码 |
| name | string | 股票名称 |
| factor_id | string | `A3` |
| factor_name | string | `连板龙头接力` |
| factor_value | float | base_score 原值（z-score 加权和） |
| score | float | sigmoid(base_score) × 100 ∈ [0, 100]（绝对评分） |
| rank | int | 当日候选池排名（1 最强） |
| signal | string | `buy / watch / hold / unfillable` |
| confidence | float | 0-1 置信度 |
| limit_up_streak | int | 当日连板数 |
| streak_band_score | float | 板高 z-score |
| concept_leader_score | float | 题材内龙头身份（0-1） |
| concept_followers_rate | float | 题材跟封率（log 归一化 0-1） |
| early_seal_score | float | 早封板得分（分钟线精确 0-1） |
| seal_strength_proxy | float | 封板强度代理（`-turnover`） |
| blow_up_proxy | float | 炸板风险（分钟线/日线 0-1） |
| position_60d | float | 60 日相对位置（0-1） |
| lhb_hotmoney | float | 龙虎榜游资净买入 |
| is_one_word | bool | t+1 一字板（不可成交参考） |
| forward_return | float | t+1 open → t+2 vwap 毛收益（缓存自带，省流量） |
| fillable | bool | t+1 可成交（非一字、非开盘涨停、非停牌） |
| next_open / vwap_t2 | float | t+1 open / t+2 vwap（诊断） |
| data_version | string | `pandadata-relay-streak3-v2` |
| update_time | datetime | ISO 8601 |

## 使用方式

```bash
# 凭证（也可写入 ~/.pandadata/pandadata.env）
export PANDA_USERNAME=<86 手机号>
export PANDA_PASSWORD=<密码>

cd alpha-A3/开发产物/scripts

# 1) 因子计算（全市场全区间，3 年）
python factor.py --start 20230619 --end 20260619 \
    --out ../../生产产物/数据库.parquet
# 可选：--with-minute 开启分钟线精确化 early_seal / blow_up

# 2) 三层沙漏验证（未来函数 / 过拟合 / 跨年度样本外）
python validate.py

# 3) 因子回测（IC / RankIC / ICIR / IR / CR / ARR / MDD / 分层 / 换手）
python backtest.py --start 20230619 --end 20260619 \
    --factor-parquet ../../生产产物/数据库.parquet \
    --out ../../生产产物/回测报告.md \
    --out-json ../../生产产物/回测指标.json

# 4) 策略层回测（双成本 × IS/OOS × 含 / 不含 IC gate）
python strategy_backtest.py --start 20230619 --end 20260619 \
    --factor-parquet ../../生产产物/数据库.parquet --top-n 3 \
    --out ../../生产产物/策略回测报告.md \
    --out-json ../../生产产物/策略回测指标.json

# 5) HTML 可视化（资金曲线 + 回撤 + IC 时序 + 月度 + Q1-Q5 柱）
python render_html.py \
    --factor-parquet ../../生产产物/数据库.parquet \
    --factor-report ../../生产产物/回测指标.json \
    --strategy-report ../../生产产物/策略回测指标.json \
    --out ../../生产产物/回测报告.html

# 附加工具：板高曲线 IS 拟合（探索性）
python calibrate_streak.py --start 20230619 --is-end 20251218 \
    --out ../streak_curve_calibrated.json
```

## 关键设计决策

| 项 | 选择 | 理由 |
|---|---|---|
| 板高门槛 | 固定 ≥3 板 | 用户拍板：纯粹高度龙头 |
| 持有周期 | T+1 进 / T+2 vwap 出 | 符合 A 股 T+1 制度 |
| 一字板处理 | 跳过（标记 unfillable） | 用户拍板：买不到就是买不到 |
| z-score 参照集 | ≥2 板（不只是 ≥3 板候选） | 候选池每日 0~3 只太窄 |
| 信号收敛 | 每日 top ≤ 3 | 事件型因子，不是横截面打分 |
| score 计算 | sigmoid 映射（绝对评分） | 跨日可比，避免"今日 ≥3 板都垃圾也有 score=100" |
| Layer 2 状态机 | **已删除**（v1/v2 经回测验证有害） | ENV gate 让胜率下降 2.2%；改为策略层 IC gate 替代 |
| 题材龙头 PIT | 概念按 `in_date ≤ signal_date` 过滤 | 防未来函数 |
| 连板停牌处理 | 停牌不算断板，复牌接续板数 | A 股口径 |
| 创业板 limit_up 兜底 | pre_close × 1.20（不是 ×1.10） | 创业板 20% 涨幅 |
| 封板顺延卖出 | 找首个未封板日 close | 杜绝按封死板价虚兑收益 |
| 股票池 | 主板 + 创业板，剔 ST/688/北交所 | 剔除规则不一致标的 |
| forward_return 缓存 | parquet 自带 | 省流量、backtest 不重拉行情 |

## 验收要求

- 不允许未来函数（Layer 1 只用 t 日及之前；t+1 行情仅参与 forward_return 评估）
- 必须通过训练/测试、跨年度样本外
- 必须输出 IC / RankIC / ICIR / IR / CR / ARR / MDD / 分层收益 / 换手率
- 标准成本 0.30% + 压力成本 0.50% 同时输出
- 必须使用 panda_data 实现
- 不通过验证不得进入生产

## 已知局限

| 项 | 状态 | 影响 |
|---|---|---|
| 候选池 IS 期次日均值负偏 | ⚠️ 数据事实 | 2-7 板 IS 期次日均值 -1.2% ~ -4.3%，纯多头亏穿 |
| **OOS 期表现远好于 IS** | ⚠️ 结构性顺风 | 2025-12 ~ 2026-06 IC=0.18、Q5-Q1=+3.78%，依赖游资活跃环境 |
| 策略层需配合 IC gate | ✅ 已实现 | strategy_backtest.py 加 60 日 RankIC > 0.02 才下单 |
| 概念全量受 600003 套餐限额 | ✅ 已绕开 | 按概念遍历逐个拉，避免单次拉满 |
| get_factor 单段最长 3 年 | ✅ 已分段 | 3 年分段拉取 |
| 分钟线流量大 | ✅ 已优化 | 只拉涨停日的 (ts_code, trade_date) 组合，流量减 80% |
