#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpha-A3 因子回测（纯因子检验，对齐官方 a00 模板风格）
================================================================
回测口径：
  - 信号在 t 日收盘后形成
  - 入场: t+1 open
  - 出场: t+2 vwap (= amount / volume，免分钟线)
  - forward_return = vwap[t+2] / open[t+1] - 1
  - 不可成交跳过: t+1 一字板 / 开盘涨停 / 停牌

报告指标（V2 规则 §6 + 官方 a00 模板）：
  - 因子预测力: IC / Rank IC / ICIR / Rank ICIR
  - 策略层近似: IR / CR / ARR(%) / MDD(%)
  - 分层收益（5 层等频）
  - 换手率
  - 信号样例
  - 样本外分段（最近 6 个月）

读取生产 parquet (factor.py 产物)，不重跑因子。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from factor import init_panda, load_daily, filter_universe, FACTOR_ID, FACTOR_NAME  # noqa: E402


ALPHA_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTOR_PARQUET = ALPHA_ROOT / "alpha-a3-streak-leader-relay-production" / "database.parquet"
DEFAULT_REPORT_MD = ALPHA_ROOT / "alpha-a3-streak-leader-relay-production" / "backtest_report.md"
DEFAULT_REPORT_JSON = ALPHA_ROOT / "alpha-a3-streak-leader-relay-production" / "backtest_metrics.json"

TRADING_DAYS_PER_YEAR = 244
OOS_MONTHS = 6
COST_STD = 0.003     # 标准双边
COST_STRESS = 0.005  # 压力双边


# ============================================================
# 数据准备
# ============================================================
def build_forward_returns(quotes: pd.DataFrame) -> pd.DataFrame:
    """因子在 t 日形成；入场 t+1 open，出场 t+2 vwap(amount/volume)。

    返回每个 (trade_date=t, ts_code) 对应的：
      - forward_return: 毛收益（不含成本）
      - fillable: t+1 可成交（非一字、非开盘涨停、非停牌）
      - vwap_t2: t+2 加权均价（用于诊断）
    """
    df = quotes.sort_values(["ts_code", "trade_date"]).copy()
    g = df.groupby("ts_code", sort=False)

    # t+1 行情
    df["next_open"] = g["open"].shift(-1)
    df["next_high"] = g["high"].shift(-1)
    df["next_low"] = g["low"].shift(-1)
    df["next_trade_status"] = g["trade_status"].shift(-1)

    # t+2 行情
    df["next2_amount"] = g["amount"].shift(-2)
    df["next2_volume"] = g["volume"].shift(-2)
    df["next2_close"] = g["close"].shift(-2)

    # eff_limit_up_next (用于一字板判定 + 开盘涨停跳过)
    pre_next = g["close"].shift(0)   # = close[t]
    rate = np.where(df["ts_code"].astype(str).str.match(r"^(300|301)\d{3}\.SZ$"), 1.20, 1.10)
    df["eff_limit_up_next"] = (pre_next * rate).round(2)

    tol = 0.999
    one_word_next = (
        df["next_open"].ge(df["eff_limit_up_next"] * tol)
        & df["next_low"].ge(df["eff_limit_up_next"] * tol)
        & df["next_high"].ge(df["eff_limit_up_next"] * tol)
    )
    open_at_limit_next = df["next_open"].ge(df["eff_limit_up_next"] * tol)
    halt_next = df["next_trade_status"].fillna(0).ne(0)

    df["fillable"] = ~(one_word_next | open_at_limit_next | halt_next) & df["next_open"].gt(0)

    # vwap[t+2]，若 t+2 缺数据则用 close 兜底
    df["vwap_t2"] = (df["next2_amount"] / df["next2_volume"]).where(
        df["next2_volume"].gt(0), df["next2_close"]
    )

    # forward_return
    df["forward_return"] = (df["vwap_t2"] / df["next_open"] - 1.0)

    out = df[["trade_date", "ts_code", "forward_return", "fillable", "next_open", "vwap_t2"]]
    return out.dropna(subset=["trade_date", "ts_code"])


def load_factor_panel(factor_parquet: Path) -> pd.DataFrame:
    df = pd.read_parquet(factor_parquet)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


# ============================================================
# 指标
# ============================================================
def _pearson_ic(g: pd.DataFrame) -> float:
    if len(g) < 3:
        return np.nan
    return float(g["factor_value"].corr(g["forward_return"]))


def _rank_ic(g: pd.DataFrame) -> float:
    if len(g) < 3:
        return np.nan
    return float(g["factor_value"].rank(method="average").corr(
        g["forward_return"].rank(method="average")))


def ic_metrics(panel: pd.DataFrame) -> dict:
    """官方风格 4 件套 IC 指标 + 每日 IC 时序（用于画图）。"""
    daily_ic = panel.groupby("trade_date").apply(_pearson_ic).dropna()
    daily_rank_ic = panel.groupby("trade_date").apply(_rank_ic).dropna()

    ic_mean = float(daily_ic.mean()) if not daily_ic.empty else 0.0
    ic_std = float(daily_ic.std()) if len(daily_ic) > 1 else 0.0
    rank_mean = float(daily_rank_ic.mean()) if not daily_rank_ic.empty else 0.0
    rank_std = float(daily_rank_ic.std()) if len(daily_rank_ic) > 1 else 0.0

    return {
        "IC": round(ic_mean, 4),
        "ICIR": round(ic_mean / ic_std, 4) if ic_std else 0.0,
        "RankIC": round(rank_mean, 4),
        "RankICIR": round(rank_mean / rank_std, 4) if rank_std else 0.0,
        "n_days": int(len(daily_ic)),
        "avg_xs_width": round(panel.groupby("trade_date").size().mean(), 2),
        # 时序：用于 HTML 画 IC 曲线
        "ic_series": [(d.strftime("%Y-%m-%d"), round(v, 6))
                      for d, v in daily_ic.items()],
        "rank_ic_series": [(d.strftime("%Y-%m-%d"), round(v, 6))
                           for d, v in daily_rank_ic.items()],
    }


def quintile_returns(panel: pd.DataFrame, n_groups: int = 5) -> dict:
    """分层收益（等频 n 层）"""
    def _qcut(g):
        if len(g) < n_groups:
            return None
        try:
            q = pd.qcut(g["factor_value"].rank(method="first"), n_groups,
                        labels=[f"Q{i+1}" for i in range(n_groups)])
            return g.assign(group=q.astype(str))
        except Exception:
            return None

    parts = [r for r in (_qcut(g) for _, g in panel.groupby("trade_date")) if r is not None]
    if not parts:
        return {f"Q{i+1}": np.nan for i in range(n_groups)}

    cat = pd.concat(parts, ignore_index=True)
    grp_mean = cat.groupby("group", observed=True)["forward_return"].mean().round(6).to_dict()
    grp_mean["Q5-Q1 (多空)"] = round(grp_mean.get(f"Q{n_groups}", 0) - grp_mean.get("Q1", 0), 6)
    return grp_mean


def buy_signal_metrics(panel: pd.DataFrame) -> dict:
    """以 buy 信号为多头组合，输出 IR / CR / ARR / MDD（官方口径）+ 资金曲线时序。"""
    buy = panel[panel["signal"] == "buy"].copy()
    buy_fill = buy[buy["fillable"]]
    if buy_fill.empty:
        return {"IR_SHR": 0.0, "CR": 0.0, "ARR_pct": 0.0, "MDD_pct": 0.0,
                "n_signals": int(len(buy)), "n_filled": 0, "fill_rate": 0.0,
                "equity_curve": [], "monthly_return": []}

    daily_ret = buy_fill.groupby("trade_date")["forward_return"].mean().fillna(0)
    if daily_ret.empty:
        return {"IR_SHR": 0.0, "CR": 0.0, "ARR_pct": 0.0, "MDD_pct": 0.0,
                "n_signals": int(len(buy)), "n_filled": int(len(buy_fill)),
                "fill_rate": round(len(buy_fill) / max(len(buy), 1), 4),
                "equity_curve": [], "monthly_return": []}

    sd = daily_ret.std()
    ir = float(daily_ret.mean() / sd * np.sqrt(TRADING_DAYS_PER_YEAR)) if sd else 0.0

    curve = (1 + daily_ret).cumprod()
    cum_ret = float(curve.iloc[-1] - 1)
    n_days = len(daily_ret)
    years = max(n_days / TRADING_DAYS_PER_YEAR, 1e-9)
    arr = (1 + cum_ret) ** (1 / years) - 1
    dd = curve / curve.cummax() - 1
    mdd = float(dd.min())
    cr = cum_ret / abs(mdd) if mdd else 0.0

    # 资金曲线 + 回撤时序（HTML 画图）
    equity_curve = [(d.strftime("%Y-%m-%d"), round(float(v), 6)) for d, v in curve.items()]
    drawdown_curve = [(d.strftime("%Y-%m-%d"), round(float(v) * 100, 4)) for d, v in dd.items()]

    # 月度收益（heatmap）
    monthly = daily_ret.groupby(pd.Grouper(freq='M')).apply(lambda x: float((1 + x).prod() - 1))
    monthly_return = [(d.strftime("%Y-%m"), round(float(v) * 100, 4)) for d, v in monthly.items()]

    return {
        "IR_SHR": round(ir, 4),
        "CR": round(cr, 4),
        "ARR_pct": round(arr * 100, 4),
        "MDD_pct": round(mdd * 100, 4),
        "n_signals": int(len(buy)),
        "n_filled": int(len(buy_fill)),
        "fill_rate": round(len(buy_fill) / max(len(buy), 1), 4),
        "n_days": int(n_days),
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
        "monthly_return": monthly_return,
    }


def turnover_rate(panel: pd.DataFrame) -> float:
    """换手率：buy 信号的每日组合 jaccard 距离均值。"""
    daily_buy = panel[panel["signal"] == "buy"].groupby("trade_date")["ts_code"].apply(set)
    vals = []
    prev = None
    for cur in daily_buy:
        if prev is not None:
            u = cur | prev
            vals.append(1 - len(cur & prev) / len(u) if u else 0.0)
        prev = cur
    return round(float(np.mean(vals)) if vals else 0.0, 4)


# ============================================================
# IS / OOS 切分
# ============================================================
def split_is_oos(panel: pd.DataFrame, oos_months: int = OOS_MONTHS) -> tuple[pd.DataFrame, pd.DataFrame]:
    if panel.empty:
        return panel, panel
    dmax = panel["trade_date"].max()
    oos_start = dmax - pd.DateOffset(months=oos_months)
    is_p = panel[panel["trade_date"] < oos_start]
    oos_p = panel[panel["trade_date"] >= oos_start]
    return is_p, oos_p


# ============================================================
# 全套指标
# ============================================================
def full_report(panel: pd.DataFrame, label: str = "ALL") -> dict:
    if panel.empty:
        return {"label": label, "样本数": 0, "评估口径": "空 panel"}

    ic = ic_metrics(panel)
    qr = quintile_returns(panel)
    bs = buy_signal_metrics(panel)
    tr = turnover_rate(panel)

    # 信号分布
    sig_dist = panel["signal"].value_counts().to_dict() if "signal" in panel else {}

    return {
        "label": label,
        "区间": f"{panel['trade_date'].min().date()} ~ {panel['trade_date'].max().date()}",
        "样本数": int(len(panel)),
        "信号分布": sig_dist,
        "IC": ic["IC"],
        "RankIC": ic["RankIC"],
        "ICIR": ic["ICIR"],
        "RankICIR": ic["RankICIR"],
        "n_days_有效IC": ic["n_days"],
        "平均横截面宽度": ic["avg_xs_width"],
        "IR(SHR*)": bs["IR_SHR"],
        "CR": bs["CR"],
        "ARR(%)": bs["ARR_pct"],
        "MDD(%)": bs["MDD_pct"],
        "buy信号数": bs["n_signals"],
        "可成交数": bs["n_filled"],
        "成交率": bs["fill_rate"],
        "回测天数": bs.get("n_days", 0),
        "换手率": tr,
        "分层收益(Q1-Q5)": qr,
        # 时序数据用于 HTML 画图（仅 ALL 用，IS/OOS 可不输出节省体积）
        "ic_series": ic.get("ic_series", []),
        "rank_ic_series": ic.get("rank_ic_series", []),
        "equity_curve": bs.get("equity_curve", []),
        "drawdown_curve": bs.get("drawdown_curve", []),
        "monthly_return": bs.get("monthly_return", []),
    }


# ============================================================
# Markdown 报告
# ============================================================
def render_markdown(reports: list[dict], cfg_summary: dict) -> str:
    lines = []
    lines.append("# Alpha-A3 连板龙头接力 · 因子回测报告\n")
    lines.append(f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 因子 parquet: `{cfg_summary['factor_parquet']}`")
    lines.append(f"- 行情区间: {cfg_summary['quote_start']} ~ {cfg_summary['quote_end']}")
    lines.append(f"- 股票池: {cfg_summary['pool_name']}")
    lines.append("")
    lines.append("## 评估口径")
    lines.append("- 信号在 t 日收盘后形成")
    lines.append("- **入场**: t+1 open（开盘价）")
    lines.append("- **出场**: t+2 vwap (= amount / volume) — 全日加权均价，模拟分批出货")
    lines.append("- **forward_return** = vwap[t+2] / open[t+1] − 1")
    lines.append("- **不可成交跳过**: t+1 一字板 / 开盘涨停 / 停牌（fillable=False）")
    lines.append("- **样本外 OOS**: 最近 6 个月")
    lines.append("- **成本**: 当前报告为 **毛收益（未扣双边成本）**，策略层另算双成本 0.30% / 0.50%")
    lines.append("")

    for r in reports:
        lines.append(f"## {r['label']}\n")
        if r["样本数"] == 0:
            lines.append(f"_{r.get('评估口径', '空 panel')}_\n")
            continue
        lines.append(f"- 区间: {r['区间']}")
        lines.append(f"- 样本数: {r['样本数']}（信号分布: {r['信号分布']}）")
        lines.append(f"- 平均横截面宽度: {r['平均横截面宽度']}（≥3 板每日候选数，⚠️ 截面过窄时 IC/ICIR 仅供参考）")
        lines.append("")
        lines.append("**因子预测力**\n")
        lines.append("| 指标 | 值 |\n|---|---|")
        lines.append(f"| IC | {r['IC']} |")
        lines.append(f"| RankIC | {r['RankIC']} |")
        lines.append(f"| ICIR | {r['ICIR']} |")
        lines.append(f"| RankICIR | {r['RankICIR']} |")
        lines.append(f"| 有效横截面日数 | {r['n_days_有效IC']} |")
        lines.append("")
        lines.append("**策略层近似（buy 多头组合，毛收益）**\n")
        lines.append("| 指标 | 值 |\n|---|---|")
        lines.append(f"| IR(SHR*) | {r['IR(SHR*)']} |")
        lines.append(f"| CR | {r['CR']} |")
        lines.append(f"| ARR(%) | {r['ARR(%)']} |")
        lines.append(f"| MDD(%) | {r['MDD(%)']} |")
        lines.append(f"| buy 信号数 / 可成交 / 成交率 | {r['buy信号数']} / {r['可成交数']} / {r['成交率']:.1%} |")
        lines.append(f"| 回测天数 | {r['回测天数']} |")
        lines.append(f"| 换手率 | {r['换手率']} |")
        lines.append("")
        lines.append("**分层收益（按 factor_value 等频 5 层）**\n")
        lines.append("| 层 | 平均 forward_return |\n|---|---|")
        for k, v in r["分层收益(Q1-Q5)"].items():
            mark = " ⭐" if k == f"Q5-Q1 (多空)" else ""
            lines.append(f"| {k} | {v}{mark} |")
        lines.append("")
    return "\n".join(lines)


# ============================================================
# 入口
# ============================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="Alpha-A3 因子回测（纯因子检验）")
    ap.add_argument("--factor-parquet", default=str(DEFAULT_FACTOR_PARQUET))
    ap.add_argument("--start", default="20230619", help="行情拉取起始（包含 buffer）")
    ap.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--indicator", default="", choices=["", "000300", "000852", "399303"])
    ap.add_argument("--out", default=str(DEFAULT_REPORT_MD))
    ap.add_argument("--out-json", default=str(DEFAULT_REPORT_JSON))
    args = ap.parse_args()

    pool_name = {"": "全A", "000300": "沪深300", "000852": "中证1000", "399303": "国证2000"}[args.indicator]
    print("=" * 64)
    print(f"Alpha-A3 因子回测 | {args.start} ~ {args.end} | 股票池={pool_name}")
    print("=" * 64)

    # 1) 读因子 parquet
    print(f"[1/4] 读因子 parquet: {args.factor_parquet}")
    factor = load_factor_panel(Path(args.factor_parquet))
    print(f"      {len(factor)} 行 × {factor.shape[1]} 列, 日期 {factor['trade_date'].min().date()} ~ {factor['trade_date'].max().date()}")

    # 2) 优先用 parquet 自带的 forward_return（factor.py 已缓存，省流量）
    if "forward_return" in factor.columns and factor["forward_return"].notna().any():
        print(f"[2/4] 使用 parquet 缓存的 forward_return（{factor['forward_return'].notna().sum()} 非空，省流量）")
        panel = factor.dropna(subset=["factor_value", "forward_return"]).copy()
    else:
        print(f"[2/4] parquet 无 forward_return，拉行情构造")
        pd_api = init_panda()
        quotes = filter_universe(load_daily(args.start, args.end, pd_api, indicator=args.indicator))
        quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
        forward = build_forward_returns(quotes)
        panel = factor.merge(forward, on=["trade_date", "ts_code"], how="inner")
        panel = panel.dropna(subset=["factor_value", "forward_return"])

    print(f"      最终 panel: {len(panel)} 行, {panel['trade_date'].nunique()} 个交易日")

    # 4) 算指标
    print(f"[4/4] 计算指标")
    is_panel, oos_panel = split_is_oos(panel, OOS_MONTHS)
    reports = [
        full_report(panel, f"ALL（全样本，{pool_name}）"),
        full_report(is_panel, f"IS 样本内（{pool_name}，最近 6 月之外）"),
        full_report(oos_panel, f"OOS 样本外（最近 6 月，{pool_name}）"),
    ]

    cfg = {
        "factor_parquet": args.factor_parquet,
        "quote_start": args.start,
        "quote_end": args.end,
        "pool_name": pool_name,
    }
    md = render_markdown(reports, cfg)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(md, encoding="utf-8")
    Path(args.out_json).write_text(json.dumps(reports, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n报告已写入: {args.out}")
    print(f"指标 JSON: {args.out_json}")

    # 控制台速览
    print()
    print("=" * 64)
    print(f"速览（股票池={pool_name}）")
    print("=" * 64)
    for r in reports:
        if r["样本数"] == 0:
            continue
        print(f"\n[{r['label']}]")
        print(f"  IC={r['IC']:+.4f}  RankIC={r['RankIC']:+.4f}  ICIR={r['ICIR']:+.4f}")
        print(f"  IR={r['IR(SHR*)']:+.4f}  CR={r['CR']:+.4f}  ARR={r['ARR(%)']:+.2f}%  MDD={r['MDD(%)']:+.2f}%")
        q15 = r["分层收益(Q1-Q5)"].get("Q5-Q1 (多空)", "NA")
        print(f"  Q5-Q1 多空={q15}  换手={r['换手率']}  成交率={r['成交率']:.1%}")


if __name__ == "__main__":
    main()
