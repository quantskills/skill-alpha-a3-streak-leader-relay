# skill-alpha-a3-streak-leader-relay

[简体中文](README.md) | **English**

> Event-driven top-N factor that identifies streak-leader candidates worth a T+1 relay from A-share ≥3 limit-up names.

> ⚠️ **Disclaimer**: This repository is a **research and educational example** only. It does NOT constitute investment advice, a guaranteed strategy, or any commitment of returns. Backtest numbers describe historical model behaviour, not real trading outcomes. Use at your own risk. See "Boundaries and Limitations" below.

## What this skill does

A3 is an **event-type, T+1 relay** factor. Each trading day it:

1. Builds a candidate pool of A-share names whose consecutive limit-up streak reaches ≥3
2. Scores each candidate using **10 sub-factors** (8 cross-sectional individual-stock + 2 market-state scalar)
3. Outputs the top-3 candidates with absolute scores ∈ [0, 100] and classifies them as `buy` (≥80), `watch` (≥50), `hold`, or `unfillable` (next-day one-word limit-up not reachable)

It is a **candidate discovery tool**, not a black-box trading strategy. The intended use is to surface event-driven names worth deeper review; downstream timing and execution rules remain the user's responsibility.

## Repository layout

```
skill-alpha-a3-streak-leader-relay/                    (this repository)
├── README.md / README.en.md / LICENSE                 declarations
├── skill-alpha-a3-streak-leader-relay/                      development artifacts
│   ├── SKILL.md / skill.json                          skill manifest with quantSkills metadata
│   ├── scripts/
│   │   ├── factor.py                                  factor computation (10 sub-factors)
│   │   ├── calibrate_weights.py                       ICIR + shrinkage weight retraining
│   │   ├── backtest.py                                factor-level IC/IR/quintile checks
│   │   ├── strategy_backtest.py                       portfolio-level backtest (top-N, IC gate, costs)
│   │   ├── render_html.py                             multi-view HTML report
│   │   └── validate.py                                three-layer hourglass validation
│   └── references/data_guide.md                       data interface notes
└── skill-alpha-a3-streak-leader-relay-production/           production artifacts
    ├── SKILL.md                                       production read-only manifest
    ├── database.parquet                               daily factor outputs
    ├── weights_calibrated.json                        optional retrained weights
    └── backtest_report.{md, html, json}               reproducible reports
```

## Data source

PandaData (`panda_data >= 0.0.9`) — interfaces used:

- `get_stock_daily` (A-share daily OHLC + limit_up / limit_down / trade_status)
- `get_concept_constituents` / `get_concept_list`
- `get_lhb_detail` (top-deal-board / Long-Tiger-list)
- `get_factor` (turnover, market cap)
- `get_stock_min` (optional, for early-seal detection)

A valid PandaData account is required. Credentials can be supplied via env vars (`PANDA_USERNAME` / `PANDA_PASSWORD`) or `~/.pandadata/pandadata.env`.

## Assumptions

- Signals form at T close, enter at T+1 open, exit at T+2 VWAP (`amount / volume`)
- T+1 one-word limit-up is treated as not-fillable and skipped
- A-share T+1 rule: positions held at T+1 close cannot be sold same-day
- Standard costs: 0.10% round-trip (broker commission ~0.05% + stamp tax 0.05%)
- Stress costs: 0.15% round-trip (above + 0.05% slippage)

## Parameters (defaults)

| Parameter | Default | Notes |
|---|---|---|
| min_streak | 3 | Candidate pool floor |
| ref_min_streak | 2 | Cross-sectional z-score reference set |
| top-N per day | 3 | Event-type signal cap |
| buy threshold (score) | 80 | Class promotion floor |
| IC gate window | 60 days | Rolling RankIC measurement |
| IC gate threshold | 0.02 | Minimum RankIC to allow trading |
| mkt_state z-score window | 60 days | Market-state scalar normalisation |
| Position weighting | score-weighted | `(score − 70) / Σ(score − 70)` |

All parameters are CLI-overridable; sub-factor weights can be retrained via `calibrate_weights.py`.

## Boundaries and Limitations

This factor is **regime-sensitive**. Specifically:

- The ≥3 limit-up candidate pool has historically shown **structural mean-reversion** in mid-2023 to late-2025
- Factor effectiveness depends heavily on **active hot-money environment**, captured imperfectly via the `mkt_state` and IC gate filters
- Out-of-sample window in current backtest is only ~6 months / ~74 buy-fillable signals — statistically thin
- All historical numbers in reports are **model artefacts**, not promises of future performance
- Live execution would face slippage, partial fills, sudden delistings, and other frictions not modelled in backtests

## What's in the box (for evaluation, not trading)

When a maintainer runs the full pipeline on PandaData data (2023-06 → 2026-06), the out-of-sample (most-recent-6-months) numbers are recorded in `production/backtest_report.html`. **These describe model behaviour on a closed historical window only**.

## Quickstart

```bash
# 1. Credentials
export PANDA_USERNAME=<phone>
export PANDA_PASSWORD=<password>
# or write to ~/.pandadata/pandadata.env

cd skill-alpha-a3-streak-leader-relay/scripts

# 2. Compute factor (3 years full A-share, ~15 minutes)
python factor.py --start 20230619 --end 20260619 \
    --out ../../skill-alpha-a3-streak-leader-relay-production/database.parquet

# 3. [Optional] Retrain sub-factor weights with ICIR + shrinkage
python calibrate_weights.py
# Re-run factor.py to load weights_calibrated.json automatically

# 4. Factor-level backtest (IC, IR, quintile, turnover)
python backtest.py --start 20230619 --end 20260619 \
    --factor-parquet ../../skill-alpha-a3-streak-leader-relay-production/database.parquet \
    --out ../../skill-alpha-a3-streak-leader-relay-production/backtest_report.md \
    --out-json ../../skill-alpha-a3-streak-leader-relay-production/backtest_metrics.json

# 5. Strategy-level backtest (top-N, IC gate, dual cost, score weighting)
python strategy_backtest.py --start 20230619 --end 20260619 --top-n 3 --weighting score \
    --factor-parquet ../../skill-alpha-a3-streak-leader-relay-production/database.parquet \
    --out ../../skill-alpha-a3-streak-leader-relay-production/strategy_backtest_report.md \
    --out-json ../../skill-alpha-a3-streak-leader-relay-production/strategy_backtest_metrics.json

# 6. HTML visualisation
python render_html.py \
    --factor-parquet ../../skill-alpha-a3-streak-leader-relay-production/database.parquet \
    --factor-report ../../skill-alpha-a3-streak-leader-relay-production/backtest_metrics.json \
    --strategy-report ../../skill-alpha-a3-streak-leader-relay-production/strategy_backtest_metrics.json \
    --out ../../skill-alpha-a3-streak-leader-relay-production/backtest_report.html
```

## License

GPL-3.0-only. See `LICENSE` for the full text.

## Acknowledgements

PandaData / PandaAI for the A-share market data interface. This project is community-contributed and not an official PandaAI / QuantSkills product.

## Maintainer

Community contribution. Open an Issue or Pull Request for questions, bug reports, or improvement proposals. No official endorsement or production-ready guarantee is implied.
