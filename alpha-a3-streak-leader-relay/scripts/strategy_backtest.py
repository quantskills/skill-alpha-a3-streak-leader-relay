#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpha-A3 策略层回测（仓位约束 + 真实可达收益）
================================================================
与 backtest.py 区别：
  - backtest.py: 因子检验（IC/IR/分层），评估因子值能否区分次日收益
  - strategy_backtest.py（本脚本）: 策略层回测，应用实际仓位约束 + 入出场规则 + 成本

执行规则:
  - 每日选 buy 信号 top-N（默认 N=3）
  - 入场: t+1 open
  - 出场: t+2 vwap (= amount / volume)
  - 不可成交跳过: t+1 一字板 / 开盘涨停 / 停牌
  - 双成本门槛: 标准 0.10%（万五佣金 + 印花税,真实费率）/ 压力 0.15%（含滑点）
  - 等权分仓（每只 1/N）
  - 当日资金 100% 使用

两组对比:
  组 A 「纯 A3」: 不看 market_state，每日 top-N
  组 B 「A3 + ENV gate」: 加速日(market_state=accel)整组 hold；其他日 top-N
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from factor import init_panda, load_daily, filter_universe  # noqa: E402
from backtest import build_forward_returns, load_factor_panel, split_is_oos, OOS_MONTHS  # noqa: E402


ALPHA_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTOR_PARQUET = ALPHA_ROOT / "alpha-a3-streak-leader-relay-production" / "database.parquet"
DEFAULT_REPORT_MD = ALPHA_ROOT / "alpha-a3-streak-leader-relay-production" / "strategy_backtest_report.md"
DEFAULT_REPORT_JSON = ALPHA_ROOT / "alpha-a3-streak-leader-relay-production" / "strategy_backtest_metrics.json"

TRADING_DAYS_PER_YEAR = 244
COST_STD = 0.0010      # 标准：万五佣金 + 印花税（A 股真实双边费率）
COST_STRESS = 0.0015   # 压力：标准 + 0.05% 滑点


# ============================================================
# 单组策略回测
# ============================================================
def select_topn_per_day(panel: pd.DataFrame, top_n: int,
                        score_threshold: float = 70,
                        ic_gate_dates: Optional[set] = None) -> pd.DataFrame:
    """每日选 score >= threshold 且可成交的 top-N 信号。
    ic_gate_dates: 允许下单的日期集合（来自滚动 IC gate）；None 表示不过滤。"""
    df = panel[(panel["score"] >= score_threshold) & (panel["fillable"])].copy()
    if ic_gate_dates is not None:
        df = df[df["trade_date"].isin(ic_gate_dates)]
    df = df.sort_values(["trade_date", "rank"])
    df = df.groupby("trade_date", group_keys=False).head(top_n)
    return df


def compute_rolling_ic_gate(panel: pd.DataFrame, window: int = 60,
                            ic_threshold: float = 0.02) -> set:
    """滚动 60 日 RankIC > threshold 的日期 → 允许下单的"alpha 窗口"。
    用过去 60 日的 RankIC 均值作为今日 gate（无未来函数）。"""
    daily_ic = (panel.groupby("trade_date")
                .apply(lambda g: g["factor_value"].rank().corr(g["forward_return"].rank())
                       if len(g) >= 3 else np.nan)
                .dropna())
    if daily_ic.empty:
        return set(panel["trade_date"].unique())   # 兜底：全允许

    # 滚动均值（不含当日；shift 1 避免未来函数）
    rolling = daily_ic.rolling(window, min_periods=window // 2).mean().shift(1)
    allowed_dates = set(rolling[rolling > ic_threshold].index)
    print(f"  [info] 滚动 IC gate: {len(allowed_dates)}/{len(daily_ic)} 天允许下单 "
          f"(window={window}, threshold={ic_threshold})")
    return allowed_dates


def _weighted_daily_return(group: pd.DataFrame, weighting: str = "equal",
                            score_floor: float = 70.0) -> float:
    """单日组合收益（按指定仓位加权）。

    weighting:
      - equal: 每只 1/N 等权
      - score: 按 (score - score_floor) 加权（score 越高仓位越大）
    """
    if len(group) == 0:
        return 0.0
    if weighting == "score" and "score" in group.columns:
        offset = (group["score"] - score_floor).clip(lower=0.01)
        total = offset.sum()
        if total > 1e-9:
            w = offset / total
            return float((group["forward_return"] * w).sum())
    # fallback / equal
    return float(group["forward_return"].mean())


def run_strategy(panel: pd.DataFrame, top_n: int, cost: float,
                 with_curve: bool = False,
                 ic_gate_dates: Optional[set] = None,
                 weighting: str = "equal") -> dict:
    """单组策略回测 → 资金曲线 + 完整指标。

    weighting: 'equal' (默认等权) / 'score' (按 score 大小加权,score 越高仓位越大)。
    """
    picks = select_topn_per_day(panel, top_n, score_threshold=70, ic_gate_dates=ic_gate_dates)
    if picks.empty:
        return {"n_signals": 0, "n_filled": 0, "fill_rate": 0.0}

    # 仓位加权聚合每日组合收益
    daily = picks.groupby("trade_date").apply(
        lambda g: _weighted_daily_return(g, weighting=weighting, score_floor=70.0)
    )
    daily_net = daily - cost   # 双边成本一次性扣

    curve = (1 + daily_net).cumprod()
    cum_ret = float(curve.iloc[-1] - 1) if not curve.empty else 0.0
    n_days = len(daily_net)
    years = max(n_days / TRADING_DAYS_PER_YEAR, 1e-9)
    arr = (1 + cum_ret) ** (1 / years) - 1

    sd = daily_net.std()
    sharpe = float(daily_net.mean() / sd * np.sqrt(TRADING_DAYS_PER_YEAR)) if sd else 0.0
    dd = curve / curve.cummax() - 1
    mdd = float(dd.min()) if not dd.empty else 0.0
    cr = cum_ret / abs(mdd) if mdd else 0.0

    # 胜率 / 赔率（单笔级别）
    picks_net = picks.assign(net_ret=picks["forward_return"] - cost)
    wins = picks_net[picks_net["net_ret"] > 0]
    losses = picks_net[picks_net["net_ret"] <= 0]
    win_rate = len(wins) / max(len(picks_net), 1)
    payoff = (wins["net_ret"].mean() / abs(losses["net_ret"].mean())) if (len(wins) and len(losses)) else np.nan

    # 换手率
    daily_set = picks.groupby("trade_date")["ts_code"].apply(set)
    tover = []
    prev = None
    for s in daily_set:
        if prev is not None:
            u = s | prev
            tover.append(1 - len(s & prev) / len(u) if u else 0.0)
        prev = s
    turnover = float(np.mean(tover)) if tover else 0.0

    result = {
        "n_signals": int(len(picks)),
        "n_days": int(n_days),
        "胜率": round(win_rate, 4),
        "赔率": round(float(payoff), 4) if not pd.isna(payoff) else None,
        "单笔均收益": round(float(picks_net["net_ret"].mean()), 6),
        "日均组合收益": round(float(daily_net.mean()), 6),
        "ARR_pct": round(arr * 100, 4),
        "夏普": round(sharpe, 4),
        "MDD_pct": round(mdd * 100, 4),
        "CR": round(cr, 4),
        "累计收益_pct": round(cum_ret * 100, 4),
        "换手率": round(turnover, 4),
    }
    if with_curve:
        result["equity_curve"] = [(d.strftime("%Y-%m-%d"), round(float(v), 6)) for d, v in curve.items()]
        result["drawdown_curve"] = [(d.strftime("%Y-%m-%d"), round(float(v) * 100, 4)) for d, v in dd.items()]
        monthly = daily_net.groupby(pd.Grouper(freq='M')).apply(lambda x: float((1 + x).prod() - 1))
        result["monthly_return"] = [(d.strftime("%Y-%m"), round(float(v) * 100, 4)) for d, v in monthly.items()]
    return result


def render_markdown(scenarios: list[dict], cfg: dict) -> str:
    L = []
    L.append("# Alpha-A3 · 策略层回测报告\n")
    L.append(f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"- 因子 parquet: `{cfg['factor_parquet']}`")
    L.append(f"- 行情区间: {cfg['quote_start']} ~ {cfg['quote_end']}")
    L.append(f"- 股票池: {cfg['pool_name']}")
    wlabel = "按 score 加权" if cfg.get("weighting") == "score" else "等权分仓"
    L.append(f"- 每日 top-N: {cfg['top_n']}（{wlabel}）")
    L.append("")
    L.append("## 执行规则")
    L.append("- **每日选股**: score >= 80 且可成交，按 rank 取 top-N")
    L.append("- **入场**: t+1 open")
    L.append("- **出场**: t+2 vwap (= amount / volume)")
    L.append("- **跳过**: t+1 一字板 / 开盘涨停 / 停牌")
    L.append("- **成本**: 标准 0.10%（万五佣金 + 印花税）/ 压力 0.15%（含滑点）— 双边一次性扣")
    L.append("- **样本外 OOS**: 最近 6 个月")
    L.append("")

    for sc in scenarios:
        L.append(f"## {sc['label']} | 成本={sc['cost']:.1%}\n")
        if sc.get("metrics", {}).get("n_signals", 0) == 0:
            L.append("_无可成交信号_\n")
            continue
        m = sc["metrics"]
        L.append("| 指标 | 值 |\n|---|---|")
        for k, v in m.items():
            L.append(f"| {k} | {v} |")
        L.append("")
    return "\n".join(L)


# ============================================================
# 入口
# ============================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="Alpha-A3 策略层回测")
    ap.add_argument("--factor-parquet", default=str(DEFAULT_FACTOR_PARQUET))
    ap.add_argument("--start", default="20230619")
    ap.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--indicator", default="", choices=["", "000300", "000852", "399303"])
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--weighting", default="score", choices=["equal", "score"],
                    help="仓位加权方式：equal 等权 / score 按 (score-70) 加权（默认 score）")
    ap.add_argument("--out", default=str(DEFAULT_REPORT_MD))
    ap.add_argument("--out-json", default=str(DEFAULT_REPORT_JSON))
    args = ap.parse_args()

    pool_name = {"": "全A", "000300": "沪深300", "000852": "中证1000", "399303": "国证2000"}[args.indicator]
    print("=" * 64)
    print(f"Alpha-A3 策略回测 | {args.start} ~ {args.end} | 股票池={pool_name} | top-{args.top_n}")
    print("=" * 64)

    # 1) 读 factor parquet
    print(f"[1/4] 读因子 parquet")
    factor = load_factor_panel(Path(args.factor_parquet))

    # 2) forward_return：优先用 parquet 缓存（factor.py 已生成），缺失时再拉行情
    if "forward_return" in factor.columns and factor["forward_return"].notna().any():
        print(f"[2/4] 使用 parquet 缓存的 forward_return（{factor['forward_return'].notna().sum()} 非空，省流量）")
        panel = factor.dropna(subset=["factor_value", "forward_return"]).copy()
    else:
        print(f"[2/4] 拉行情构造 forward_return（parquet 无缓存）")
        pd_api = init_panda()
        quotes = filter_universe(load_daily(args.start, args.end, pd_api, indicator=args.indicator))
        quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
        forward = build_forward_returns(quotes)
        print(f"[3/4] 合并 panel")
        panel = factor.merge(forward, on=["trade_date", "ts_code"], how="inner")
        panel = panel.dropna(subset=["factor_value", "forward_return"])
    print(f"      {len(panel)} 行")

    # 4) 双成本 × IS/OOS × 含/不含 IC gate
    print(f"[4/4] 跑策略组合")
    is_panel, oos_panel = split_is_oos(panel, OOS_MONTHS)

    # 在全样本上算滚动 IC gate（用全样本避免数据浪费，gate 本身用 shift(1) 无未来函数）
    print("  [info] 计算滚动 IC gate ...")
    ic_gate_dates = compute_rolling_ic_gate(panel, window=60, ic_threshold=0.02)

    scenarios = []
    for label_panel, panel_part in [("ALL 全样本", panel), ("IS 样本内", is_panel), ("OOS 样本外", oos_panel)]:
        for cost, cost_label in [(COST_STD, "标准 0.10%"), (COST_STRESS, "压力 0.15%")]:
            for gate_on, gate_label in [(False, "组A 纯A3"), (True, "组B 含IC gate")]:
                # 三个区间 (ALL/IS/OOS) 标准成本下都生成曲线，供 HTML 多维度展示
                with_curve = (cost == COST_STD)
                gate_arg = ic_gate_dates if gate_on else None
                m = run_strategy(panel_part, top_n=args.top_n, cost=cost,
                                 with_curve=with_curve, ic_gate_dates=gate_arg,
                                 weighting=args.weighting)
                scenarios.append({
                    "label": f"{gate_label} · {label_panel} · 成本={cost_label}",
                    "cost": cost, "ic_gate": gate_on,
                    "metrics": m,
                })

    cfg = {
        "factor_parquet": args.factor_parquet,
        "quote_start": args.start, "quote_end": args.end,
        "pool_name": pool_name, "top_n": args.top_n, "weighting": args.weighting,
    }
    md = render_markdown(scenarios, cfg)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(md, encoding="utf-8")
    Path(args.out_json).write_text(json.dumps(scenarios, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"\n报告已写入: {args.out}")
    print(f"指标 JSON: {args.out_json}")

    # 控制台速览
    print()
    print("=" * 64)
    print(f"速览（标准成本 0.10%）")
    print("=" * 64)
    for sc in scenarios:
        if "标准" not in sc["label"]: continue
        m = sc.get("metrics", {})
        if m.get("n_signals", 0) == 0:
            print(f"  {sc['label']}: 无可成交"); continue
        print(f"  {sc['label']}:")
        print(f"    胜率={m['胜率']:.1%}  夏普={m['夏普']:+.2f}  ARR={m['ARR_pct']:+.2f}%  MDD={m['MDD_pct']:+.2f}%  累计={m['累计收益_pct']:+.2f}%")


if __name__ == "__main__":
    main()
