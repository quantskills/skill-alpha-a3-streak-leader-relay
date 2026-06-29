#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpha-A3 子因子权重校准（ICIR 加权）
================================================================
读取已有 parquet，对每个子因子计算 IS 期的 IC 均值/std/ICIR，
按 ICIR 大小+符号归一为新权重，输出到 weights_calibrated.json。

factor.py 在不传 --no-calibrated 时会自动加载这个 JSON。

使用:
  python3 calibrate_weights.py                              # 默认 IS = train_end 之前的全部
  python3 calibrate_weights.py --train-end 20251219         # 自定义 IS 终止日
  python3 calibrate_weights.py --method icir --target-sum 1.0
  python3 calibrate_weights.py --dry-run                    # 只打印不写入
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ALPHA_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARQUET = ALPHA_ROOT / "skill-alpha-a3-streak-leader-relay-production" / "database.parquet"
DEFAULT_OUT = ALPHA_ROOT / "skill-alpha-a3-streak-leader-relay-production" / "weights_calibrated.json"

# 子因子 → parquet 字段映射 + 期望符号（先验，校准如果反号会发出警告）
# (raw_field, expected_sign)：+1 = 期望与 forward_return 正相关，-1 = 期望负相关
CROSS_SECTION_FACTORS: dict[str, tuple[str, int]] = {
    "streak_band":       ("limit_up_streak",       +1),
    "leader":            ("concept_leader_score",  +1),
    "early_seal":        ("early_seal_score",      +1),
    "seal_strength":     ("seal_strength_proxy",   +1),   # 已是 -turnover，符号已翻
    "blow_up":           ("blow_up_proxy",         -1),
    "position":          ("position_60d",          -1),
    "lhb":               ("lhb_hotmoney",          +1),
    "concept_followers": ("concept_followers_rate", +1),
}

# 大盘情绪：标量子因子（每日全市场同值），不能做截面 IC
# 保留固定先验权重，不参与校准
MARKET_STATE_PRIOR: dict[str, float] = {
    "mkt_lu":      +0.10,
    "mkt_blow_up": -0.10,
}


def cross_section_ic(panel: pd.DataFrame, factor_field: str,
                     ret_field: str = "forward_return",
                     date_field: str = "signal_date") -> pd.Series:
    """每日横截面 Spearman IC（rank correlation）。"""
    sub = panel[[date_field, factor_field, ret_field]].dropna()
    if sub.empty:
        return pd.Series(dtype=float)
    ic_by_date = sub.groupby(date_field).apply(
        lambda g: g[factor_field].rank().corr(g[ret_field].rank())
        if len(g) >= 3 else np.nan
    )
    return ic_by_date.dropna()


def compute_icir_table(panel: pd.DataFrame) -> pd.DataFrame:
    """对每个个股截面子因子算 IC 均值/std/ICIR/有效日数。"""
    rows = []
    for w_key, (field, expected_sign) in CROSS_SECTION_FACTORS.items():
        if field not in panel.columns:
            rows.append({
                "factor": w_key, "field": field, "expected_sign": expected_sign,
                "ic_mean": np.nan, "ic_std": np.nan, "icir": np.nan,
                "n_days": 0, "sign_match": False,
            })
            continue
        ic_series = cross_section_ic(panel, field)
        if len(ic_series) < 20:
            rows.append({
                "factor": w_key, "field": field, "expected_sign": expected_sign,
                "ic_mean": float(ic_series.mean()) if len(ic_series) else np.nan,
                "ic_std":  float(ic_series.std())  if len(ic_series) else np.nan,
                "icir":    np.nan,
                "n_days": int(len(ic_series)),
                "sign_match": False,
            })
            continue
        ic_mean = float(ic_series.mean())
        ic_std = float(ic_series.std())
        icir = ic_mean / ic_std if ic_std > 1e-9 else 0.0
        sign_match = (icir > 0 and expected_sign > 0) or (icir < 0 and expected_sign < 0)
        rows.append({
            "factor": w_key, "field": field, "expected_sign": expected_sign,
            "ic_mean": ic_mean, "ic_std": ic_std, "icir": icir,
            "n_days": int(len(ic_series)), "sign_match": sign_match,
        })
    return pd.DataFrame(rows)


PRIOR_WEIGHTS: dict[str, float] = {
    # 个股截面子因子的先验权重（与 factor.py DEFAULT_CONFIG 一致）
    "streak_band":       +0.18,
    "leader":            +0.18,
    "early_seal":        +0.13,
    "seal_strength":     +0.09,
    "blow_up":           -0.13,
    "position":          -0.09,
    "lhb":               +0.05,
    "concept_followers": +0.05,
}


def icir_to_weights(icir_table: pd.DataFrame, target_abs_sum: float = 1.0,
                    min_abs_weight: float = 0.02,
                    shrinkage: float = 0.5,
                    max_abs_weight: float | None = 0.25) -> dict[str, float]:
    """ICIR 大小+符号 → 归一化权重（带 shrinkage 防过拟合）。

    流程:
    1. 用 ICIR 算"信号方向 × 强度",归一化到 target_abs_sum
    2. shrinkage 收缩: w = (1-shrinkage)*icir_w + shrinkage*prior_w
       - shrinkage=0.0: 纯 ICIR（最激进，最易过拟合）
       - shrinkage=0.5: 一半信号一半先验（推荐）
       - shrinkage=1.0: 纯先验（不变）
    3. max_abs_weight: 单权重绝对值上限，防止某个子因子主导
    """
    raw = {}
    for _, r in icir_table.iterrows():
        if pd.isna(r["icir"]) or r["icir"] == 0:
            raw[r["factor"]] = r["expected_sign"] * min_abs_weight
        else:
            raw[r["factor"]] = float(r["icir"])

    # 归一化到 target_abs_sum
    total_abs = sum(abs(v) for v in raw.values())
    if total_abs > 1e-9:
        scale = target_abs_sum / total_abs
        icir_w = {k: v * scale for k, v in raw.items()}
    else:
        icir_w = raw.copy()

    # Shrinkage 收缩到先验
    blended = {}
    for k, v in icir_w.items():
        prior = PRIOR_WEIGHTS.get(k, 0.0)
        blended[k] = (1 - shrinkage) * v + shrinkage * prior

    # 单权重绝对值上限
    if max_abs_weight is not None and max_abs_weight > 0:
        for k in list(blended.keys()):
            if abs(blended[k]) > max_abs_weight:
                blended[k] = max_abs_weight * (1 if blended[k] > 0 else -1)

    return {k: round(v, 4) for k, v in blended.items()}


def main():
    ap = argparse.ArgumentParser(description="A3 子因子权重 ICIR 校准")
    ap.add_argument("--parquet", default=str(DEFAULT_PARQUET))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--train-start", default=None,
                    help="IS 起始日 YYYYMMDD (默认 parquet 第一天)")
    ap.add_argument("--train-end", default=None,
                    help="IS 终止日 YYYYMMDD (默认 parquet 最新日减 6 个月，即留出 OOS 段)")
    ap.add_argument("--method", default="icir", choices=["icir"],
                    help="校准方法（当前仅 ICIR，后续可扩展 GBDT / Sharpe）")
    ap.add_argument("--target-abs-sum", type=float, default=1.0,
                    help="所有权重绝对值之和（个股截面 8 项，不含大盘情绪 2 项）")
    ap.add_argument("--shrinkage", type=float, default=0.5,
                    help="先验收缩系数 [0,1]: 0=纯ICIR最激进，1=纯先验，0.5=平衡（默认 0.5）")
    ap.add_argument("--max-abs-weight", type=float, default=0.25,
                    help="单个权重的绝对值上限，防止单因子主导（默认 0.25）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写入 JSON")
    args = ap.parse_args()

    parquet_path = Path(args.parquet)
    if not parquet_path.exists():
        print(f"❌ parquet 不存在: {parquet_path}")
        return

    print("=" * 64)
    print(f"A3 子因子权重校准 · {args.method.upper()} 方法")
    print("=" * 64)
    df = pd.read_parquet(parquet_path)
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    print(f"  原始 parquet: {len(df)} 行 / {df['signal_date'].nunique()} 交易日 / "
          f"{df['signal_date'].min().date()} ~ {df['signal_date'].max().date()}")

    # 切训练集
    if args.train_start:
        df = df[df["signal_date"] >= pd.Timestamp(args.train_start)]
    if args.train_end:
        end_d = pd.Timestamp(args.train_end)
    else:
        end_d = df["signal_date"].max() - pd.DateOffset(months=6)
    df = df[df["signal_date"] <= end_d]

    if df.empty:
        print(f"❌ 训练集为空（train_end={end_d.date()}）")
        return

    print(f"  训练集: {len(df)} 行 / {df['signal_date'].min().date()} ~ {df['signal_date'].max().date()}")
    print()

    # 计算 ICIR 表
    icir_table = compute_icir_table(df)
    print("📊 ICIR 表 (个股截面子因子):")
    print(icir_table.to_string(index=False))
    print()

    # 警告：符号不匹配的子因子
    mismatches = icir_table[~icir_table["sign_match"] & icir_table["icir"].notna()]
    if len(mismatches) > 0:
        print("⚠️  以下子因子的 ICIR 符号与先验不符，将按 ICIR 实际方向加权（覆盖先验）:")
        for _, r in mismatches.iterrows():
            print(f"    {r['factor']:20s}  ICIR={r['icir']:+.4f}  先验符号={r['expected_sign']:+d}")
        print()

    # 计算权重
    cs_weights = icir_to_weights(
        icir_table,
        target_abs_sum=args.target_abs_sum,
        shrinkage=args.shrinkage,
        max_abs_weight=args.max_abs_weight,
    )
    weights = {**cs_weights, **MARKET_STATE_PRIOR}

    print("✅ 校准后权重:")
    for k, v in weights.items():
        marker = "  (先验保留)" if k in MARKET_STATE_PRIOR else ""
        print(f"    {k:20s}  {v:+.4f}{marker}")
    print(f"  个股截面 |Σw| = {sum(abs(v) for v in cs_weights.values()):.4f}")
    print()

    # 写入 JSON
    if args.dry_run:
        print("--dry-run 不写入文件")
        return

    output = {
        "method": args.method,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "train_range": f"{df['signal_date'].min().date()} ~ {df['signal_date'].max().date()}",
        "train_n_rows": int(len(df)),
        "train_n_days": int(df['signal_date'].nunique()),
        "target_abs_sum": args.target_abs_sum,
        "shrinkage": args.shrinkage,
        "max_abs_weight": args.max_abs_weight,
        "weights": weights,
        "icir_table": [
            {k: (None if pd.isna(v) else (bool(v) if isinstance(v, np.bool_) else float(v) if isinstance(v, (int, float, np.floating)) else v))
             for k, v in r.items()}
            for r in icir_table.to_dict(orient="records")
        ],
        "market_state_prior": MARKET_STATE_PRIOR,
        "notes": "ICIR 加权: w[i] = ICIR[i] / Σ|ICIR| × target_abs_sum；"
                 "大盘情绪标量保留先验权重（截面 IC 不适用）",
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"📦 已写入: {out_path}")
    print(f"  下一步: 跑 factor.py（会自动加载此 JSON）")


if __name__ == "__main__":
    main()
