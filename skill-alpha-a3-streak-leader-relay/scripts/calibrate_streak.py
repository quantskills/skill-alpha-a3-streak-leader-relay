#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板高评分曲线 IS 拟合
================================================================
用 IS 样本（前 2.5 年，2023-06 ~ 2025-12）：
  - 对每个板高 N（2, 3, 4, 5, 6, 7, ...）
  - 算"板高 N 的票"次日 forward_return（t+1 open → t+2 vwap）的均值
  - 作为该板高的"评分原值"
  - 标准化到 [-1, +1] 区间作为权重曲线
  - 写入 cfg["streak_band_curve"]，供 factor.py 调用

不依赖 factor.py 已运行的 parquet，自己拉日线 + 算 streak。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from factor import (init_panda, load_daily, filter_universe, build_streak,  # noqa: E402
                    DEFAULT_CONFIG)
from backtest import build_forward_returns  # noqa: E402


ALPHA_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_JSON = ALPHA_ROOT / "skill-alpha-a3-streak-leader-relay" / "streak_curve_calibrated.json"


def main():
    ap = argparse.ArgumentParser(description="板高评分曲线 IS 拟合")
    ap.add_argument("--start", default="20230619")
    ap.add_argument("--is-end", default="20251218", help="IS 样本截止日（OOS 不参与）")
    ap.add_argument("--indicator", default="", choices=["", "000300", "000852", "399303"])
    ap.add_argument("--max-streak", type=int, default=15, help="评分曲线覆盖到的最高板高")
    ap.add_argument("--out", default=str(DEFAULT_OUT_JSON))
    args = ap.parse_args()

    print("=" * 64)
    print(f"板高评分曲线 IS 拟合 | {args.start} ~ {args.is_end}")
    print("=" * 64)

    # 1) 拉行情 + 算 streak
    print("[1/3] 拉 IS 期间全市场日线 ...")
    pd_api = init_panda()
    daily = filter_universe(load_daily(args.start, args.is_end, pd_api,
                                       indicator=args.indicator))
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    print(f"      {len(daily)} 行 / {daily['ts_code'].nunique()} 标的")

    streak = build_streak(daily, DEFAULT_CONFIG)
    print(f"      连板状态机算完: {(streak['limit_up_streak'] >= 2).sum()} 个 ≥2 板样本")

    # 2) 算 forward_return
    print("[2/3] 算 forward_return (t+1 open → t+2 vwap) ...")
    forward = build_forward_returns(daily)
    panel = streak.merge(forward, on=["trade_date", "ts_code"], how="inner")
    panel = panel.dropna(subset=["forward_return"])
    panel = panel[panel["fillable"]]   # 仅可成交样本
    print(f"      最终样本: {len(panel)} 行")

    # 3) 按板高分组算 forward_return 均值
    print("[3/3] 按板高分组算均值 ...")
    print()
    print(f"{'板高 N':8s} | {'样本数':10s} | {'avg_fwd_return':15s} | {'胜率':8s} | {'std':8s}")
    print("-" * 75)

    rows = []
    for n in range(2, args.max_streak + 1):
        sub = panel[panel["limit_up_streak"] == n]
        if len(sub) < 5:
            continue
        mean_ret = float(sub["forward_return"].mean())
        win_rate = float((sub["forward_return"] > 0).mean())
        std_ret = float(sub["forward_return"].std())
        rows.append({"streak": n, "n": len(sub),
                     "avg_fwd_return": mean_ret, "win_rate": win_rate,
                     "std": std_ret})
        print(f"{n:8d} | {len(sub):10d} | {mean_ret*100:+13.4f}% | {win_rate*100:6.2f}% | {std_ret*100:6.4f}%")

    if not rows:
        print("\n[FAIL] 无足够样本")
        return

    df = pd.DataFrame(rows)

    # 4) 把 avg_fwd_return 标准化为 [-1, +1] 评分曲线
    # 方法：min-max 归一化到 [-1, +1]
    raw = df["avg_fwd_return"].values
    if raw.max() != raw.min():
        # 中心化：将平均值在 0 附近的板高对应到 0；最高对应 +1、最低对应 -1
        midpoint = (raw.max() + raw.min()) / 2
        half_range = (raw.max() - raw.min()) / 2
        df["streak_score"] = (raw - midpoint) / half_range
    else:
        df["streak_score"] = 0.0

    print()
    print(f"{'板高 N':8s} | {'streak_score':12s}")
    print("-" * 30)
    for _, r in df.iterrows():
        print(f"{int(r['streak']):8d} | {r['streak_score']:+10.4f}")

    # 5) 落盘 JSON
    curve = {int(r.streak): round(float(r.streak_score), 4) for r in df.itertuples()}
    # 兜底：≥max_streak 板按最末值；< min 按 0
    out = {
        "fitted_at": datetime.now().isoformat(),
        "start": args.start,
        "is_end": args.is_end,
        "indicator": args.indicator,
        "n_samples": int(df["n"].sum()),
        "curve": curve,
        "default_below_min": 0.0,
        "default_above_max": float(df["streak_score"].iloc[-1]),
        "raw_stats": [
            {"streak": int(r.streak), "n": int(r.n),
             "avg_fwd_return": round(float(r.avg_fwd_return), 6),
             "win_rate": round(float(r.win_rate), 4),
             "std": round(float(r.std), 6)}
            for r in df.itertuples()
        ],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"曲线已保存: {args.out}")


if __name__ == "__main__":
    main()
