# A3 连板龙头接力 · 数据指南

## 1. 数据源

| 项目 | 说明 |
|---|---|
| 数据拉取库 | **panda_data** ≥ 0.0.9 |
| 安装 | `pip install --upgrade panda_data` |
| 认证 | `panda_data.init_token(username, password)` |
| 凭证 | 环境变量 `PANDA_USERNAME` / `PANDA_PASSWORD`（不提交 Git） |
| 网关 base_url | `http://pandadata.pandaaiquant.com`（0.0.9 默认，无需显式传） |
| 正式输入 | 仅 panda_data，不接受手工整理数据 |

## 2. 使用接口与字段

### 2.1 `get_stock_daily`（核心 · 全市场日线）

入参：`symbol=[], start_date, end_date, fields=[], st=False, indicator=""`。
- `indicator=""` 全 A / `"000300"` 沪深300 / `"000852"` 中证1000 / `"399303"` 国证2000
- 单次日期跨度 ≤ 5 年；factor.py 默认按 30 天分段，超限重试

返回字段：`date / symbol / name / open / close / high / low / volume / amount / pre_close / limit_up / limit_down / trade_status`。

- `trade_status = 0` 正常交易；非 0 视为停牌
- 价格不复权；A3 仅做"当日内 / 相邻日比值"，不跨除权日长跨度比价

### 2.2 `get_stock_min`（候选增强 · 历史 1m 分钟线）

入参：`symbol, start_date, end_date, frequency='1m', fields=[]`。
- factor.py 的 `load_minute()` 默认 **only_limit_days=True**：只对 (ts_code, trade_date) where is_limit_up_close=True 的组合拉，流量减 80%
- 用于精确提取：
  - `early_seal_score` = `(241 - first_seal_minute) / 241`
  - `blow_up_proxy` = 封→开切换次数 / 10
- 关闭时（不传 `--with-minute`）：用日线 (open-pre)/(limit_up-pre) 和 (limit_up-low)/limit_up 近似

### 2.3 `get_concept_constituents`（题材龙头）

入参：`concept, concept_stock, date, fields=[]`。
- **关键策略**：先 `get_concept_list` 拉 200 个概念列表，再按概念**遍历逐个拉成分股**——避开"无过滤全量拉"触发的 600003 套餐限额
- 返回 `concept / concept_stock(=ts_code) / date(=in_date)`
- **PIT 过滤**：保留 `in_date`，下游按 `in_date ≤ signal_date` 过滤，杜绝未来纳入回填历史

### 2.4 `get_lhb_detail`（游资共振）

入参：`start_date, end_date, side='buy', fields=[]`。
- factor.py 按 **31 天/段** 分段拉取（单段全市场 1500+ 行 0.8s）
- 剔除机构 / 股通席位（agency 含 `机构专用 / 股通专用 / 沪股通 / 深股通 / 港股通`）
- 净买入 = Σb_value − Σs_value，作为 `lhb_hotmoney` 子项；缺失填 0

### 2.5 `get_factor`（流通市值 / 换手 / 封板强度代理）

入参：`symbol="", start_date, end_date, factors=[...]`，`factors` **不能为空**。
- 单段最长 **3 年**，超 5 年触发 100008 限额；factor.py 按 3 年分段拉取
- 流通股本反推：`circ_share ≈ market_cap / close`（注意 close 为后复权，仅用于比值）
- 封板强度代理：`seal_strength_proxy = -turnover`（涨停日换手低 → 抛压小 → 封板强）

### 2.6 `get_trade_cal`（交易日历，可选）

`trade_exec_date = signal_date 的下一交易日`。开发可用日线里出现过的交易日序列推下一日。

## 3. 关键计算口径

### 3.1 涨停判定
```python
eff_limit_up = limit_up if limit_up > 0 else round(pre_close * rate, 2)
   rate = 1.20 if (300/301.SZ 创业板) else 1.10
is_limit_up_close = (trade_status == 0) & (close >= eff_limit_up * 0.999)
limit_up_streak = 连续 is_limit_up_close 累加；停牌日跳过不算断板（A 股口径），复牌后接续上一交易日板数
```

### 3.2 候选池 & z-score 参照集
- **候选**：`limit_up_streak >= 3`（固定门槛）
- **z-score 参照集**：`limit_up_streak >= 2`（缓解候选池过窄）
- 在更宽的 ≥2 板集合上算 z-score，再在 ≥3 板候选内组合排名
- **信号收敛**：每日只保留 rank ≤ `max_signals_per_day=3`（剔除"矮子里挑高个"伪信号）

### 3.3 score 计算（绝对评分，不是横截面百分位）
```python
score = 100 / (1 + exp(-2 * base_score))
# base_score = -1.5 → score=5
# base_score = -0.5 → score=27
# base_score =  0   → score=50
# base_score = +0.5 → score=73
# base_score = +1.0 → score=88
# base_score = +1.5 → score=95
```
跨日可比，避免"今日候选都垃圾也会有 score=100"。

### 3.4 forward_return 缓存口径
```python
entry = open[t+1]        # T+1 开盘买入
exit  = amount[t+2] / volume[t+2]   # T+2 全日 vwap = amount/volume 卖出
                                     # （T+2 才能卖，符合 A 股 T+1 制度）
forward_return = exit / entry - 1.0   # 毛收益，不含成本
```

**fillable 跳过判定**（T+1）：
- `is_one_word(t+1)` 一字板
- `open[t+1] >= eff_limit_up[t+1] * 0.999` 开盘涨停
- `trade_status[t+1] != 0` 停牌

### 3.5 标准化轴向
| 量 | 轴 | 实现 |
|---|---|---|
| Layer 1 子项 z-score | 股票轴（横截面） | `groupby('signal_date')` z-score |
| 滚动 IC gate（策略层） | 时间轴（时序） | `rolling(60).mean().shift(1)` |

## 4. 数据清洗

| 项 | 处理 |
|---|---|
| 股票池 | 白名单保留 `600/601/603/605.SH` + `000/001/002/003.SZ` + `300/301.SZ`；688 / .BJ / B 股不在白名单被剔除 |
| 涨停价兜底 | limit_up 缺失时：创业板 (300/301) `pre_close × 1.20`、主板 `pre_close × 1.10`，并 `round(2)` |
| ST | `st=False` + name 含 `ST`/`*` 二次剔除 |
| 停牌 | `trade_status != 0` 不参与涨停判定与成交；连板 streak 跳过停牌日 |
| 接口超限 | 日线 30 天/段；龙虎榜 31 天/段；get_factor 3 年/段；分钟线只拉涨停日；600003/504/Gateway Time-out 自动重试 |
| 流量管控 | factor.py 主动控制单次请求大小；500009 单日总流量超限需等套餐重置 |

## 5. 已知接口限制（实测）

| 接口 | 限制 | 应对 |
|---|---|---|
| `get_concept_constituents(concept="")` | 单次"全部"返回触发 600003 | 按概念遍历逐个拉 |
| `get_factor(symbol="")` | 单段 ≤ 5 年（≥3 年实测稳） | 按 3 年分段 |
| `get_lhb_detail` | 单次大跨度可能超限 | 按 31 天分段 |
| 所有接口 | 单日总流量上限（套餐相关） | 重要：合理规划，避免一次跑爆；流量爆需等 0:00 重置 |

## 6. 校准产物

- `开发产物/streak_curve_calibrated.json`：板高 IS 拟合曲线（由 `calibrate_streak.py` 生成）。
  - 当前结论：2-7 板候选样本足够、avg_fwd_return 全负；7+ 板样本不足噪音大，曲线**暂不启用**
  - factor.py 当前用线性 z-score 计算板高得分（向后兼容）

## 7. 版本

- `data_version = pandadata-relay-streak3-v2`
- `update_time` ISO 8601
