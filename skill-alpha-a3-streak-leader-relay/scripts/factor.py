#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpha-A3: 连板龙头接力因子
================================================================
三轴混合体（务必区分标准化轴向，见 CONTRACT.md §0）：
  Layer 1  横截面强度打分  —— 子项 z-score 跨股票（当日 ≥2 板参照集）
  Layer 2  时序市场状态机  —— div_z/cons_z 沿时间轴 rolling(60)
  Layer 3  事件触发        —— 早盘换手前五"第一个封板"（回测/生产用，见 backtest）

行键 = signal_date(t)；t 日收盘后形成候选，t+1 开盘买、t+1 收盘卖（封板顺延 t+2）。
market_state 属于 t+1（9:25 竞价后），广播 join 回 t 行。

数据源：PandaData（panda_data）。凭证用环境变量 PANDA_USERNAME / PANDA_PASSWORD。
优雅降级：concepts/lhb/minute/mktcap 任一缺失不影响主流程（对应子项填 0）。
"""
from __future__ import annotations

import argparse
import json
import os
import time
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# ============================================================
# 常量 & 默认配置（与 CONTRACT.md §3 一致）
# ============================================================
FACTOR_ID = "A3"
FACTOR_NAME = "连板龙头接力"
DEFAULT_DATA_VERSION = "pandadata-relay-streak3-v2"
ASSET_TYPE = "stock"

DAILY_CHUNK_DAYS = int(os.getenv("A3_DAILY_CHUNK_DAYS", "30"))   # 旧网关 504 易发，30 天/段稳妥
SYMBOL_BATCH = int(os.getenv("A3_SYMBOL_BATCH", "300"))

DEFAULT_CONFIG: dict = {
    "min_streak": 3,
    "ref_min_streak": 2,
    "weights": {
        "streak_band": 0.18,   # 板高（倒 U 型曲线，由 IS 拟合）
        "leader": 0.18,
        "early_seal": 0.13,
        "seal_strength": 0.09,
        "blow_up": -0.13,
        "position": -0.09,
        "lhb": 0.05,
        "concept_followers": 0.05,
        "mkt_lu": 0.10,           # 新增：大盘当日涨停家数 60d z-score（情绪正向）
        "mkt_blow_up": -0.10,     # 新增：大盘当日炸板率 60d z-score（情绪反向）
    },
    "mkt_state_window": 60,        # 大盘情绪 z-score 滚动窗口（交易日）
    # 信号收敛：每日最多输出 top-N 信号（事件型因子，不是横截面）
    "max_signals_per_day": 3,
    # 绝对评分阈值（base_score 经 sigmoid 映射后的 score 0-100）
    # base_score = +0.5 → score=73, +1.0 → score=88, +1.5 → score=95
    "buy_th": 70,
    "watch_th": 50,
    "limit_tol": 0.999,
    "cost": 0.0010, "cost_stress": 0.0015,  # 万五佣金+印花税 真实费率
}

ALPHA_ROOT = Path(__file__).resolve().parents[2]   # scripts → dev dir → repo root
DEFAULT_OUT = ALPHA_ROOT / "skill-alpha-a3-streak-leader-relay-production" / "database.parquet"
DEFAULT_WEIGHTS_JSON = ALPHA_ROOT / "skill-alpha-a3-streak-leader-relay-production" / "weights_calibrated.json"


def load_calibrated_weights(path: Path | None = None) -> dict | None:
    """加载校准后的权重 JSON（calibrate_weights.py 输出）。

    JSON 结构: {"method": "icir", "trained_at": "...", "train_range": "...",
                "weights": {"streak_band": 0.18, "leader": 0.20, ...}}

    返回 weights dict 或 None（未找到/无效）。
    """
    p = path or DEFAULT_WEIGHTS_JSON
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        w = data.get("weights")
        if not isinstance(w, dict) or not w:
            return None
        return w
    except Exception as e:
        print(f"  [warn] 加载 {p.name} 失败 ({e})，回退 DEFAULT_CONFIG")
        return None


def apply_weights(cfg: dict, weights_override: dict | None) -> dict:
    """把外部 weights 覆盖到 cfg 副本，返回新 cfg。"""
    if not weights_override:
        return cfg
    new_cfg = {**cfg, "weights": {**cfg["weights"], **weights_override}}
    return new_cfg


# ============================================================
# 通用工具
# ============================================================
def _compact(d: Any) -> str:
    s = str(d).strip().replace("-", "")
    return s[:8]


def _norm_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.replace(r"\.0$", "", regex=True), errors="coerce")


def _chunks(items: list, size: int) -> list[list]:
    size = max(1, size)
    return [items[i:i + size] for i in range(0, len(items), size)]


def _is_retryable(exc: Exception) -> bool:
    t = str(exc)
    return ("600003" in t) or ("超过套餐限额" in t) or ("查询结果为空" in t) or ("429" in t) or ("503" in t) or ("504" in t) or ("Gateway Time-out" in t)


def _xs_zscore(values: pd.Series, by: pd.Series) -> pd.Series:
    """横截面 z-score：按 `by`（=signal_date）跨股票标准化。Layer 1 专用。"""
    g = values.groupby(by)
    mean = g.transform("mean")
    std = g.transform("std").replace(0, np.nan)
    return ((values - mean) / std).clip(-5, 5)


def _load_streak_curve(path: Path | None = None) -> dict | None:
    """读 calibrate_streak.py 生成的板高评分曲线。"""
    import json as _json
    p = path or (ALPHA_ROOT / "skill-alpha-a3-streak-leader-relay" / "streak_curve_calibrated.json")
    if not p.exists():
        return None
    try:
        data = _json.loads(Path(p).read_text(encoding="utf-8"))
        curve_raw = data.get("curve", {})
        return {int(k): float(v) for k, v in curve_raw.items()}
    except Exception:
        return None


def _consecutive_true_streak(flags: pd.Series) -> pd.Series:
    f = flags.fillna(False).astype(bool)
    grp = f.ne(f.shift(fill_value=False)).cumsum()
    streak = f.groupby(grp).cumcount().add(1)
    return streak.where(f, 0).astype(int)


# ============================================================
# 数据层（PandaData 封装）
# ============================================================
def _load_pandadata_env() -> None:
    """文档承诺凭证可放 ~/.pandadata/pandadata.env，这里 best-effort 加载。"""
    env_file = Path.home() / ".pandadata" / "pandadata.env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def init_panda() -> Any:
    try:
        import panda_data
    except ModuleNotFoundError as exc:
        raise RuntimeError("无法导入 panda_data，请先 `pip install --upgrade panda_data`") from exc
    _load_pandadata_env()
    user = (os.getenv("PANDA_USERNAME") or os.getenv("PANDA_DATA_USERNAME")
            or os.getenv("DEFAULT_USERNAME"))
    pwd = (os.getenv("PANDA_PASSWORD") or os.getenv("PANDA_DATA_PASSWORD")
           or os.getenv("DEFAULT_PASSWORD"))
    if not (user and pwd):
        raise RuntimeError("缺少 PANDA_USERNAME / PANDA_PASSWORD 环境变量 "
                           "(也可写入 ~/.pandadata/pandadata.env 的 DEFAULT_USERNAME / DEFAULT_PASSWORD)")
    # panda_data 0.0.9 默认 base_url = pandaaiquant.com（已是当前可用网关）；
    # 若 PandaAI 启用别的网关，可用 PANDA_BASE_URL 覆盖。
    base_url = os.getenv("PANDA_BASE_URL") or os.getenv("JAVA_SERVICE_BASE_URL")
    if base_url:
        panda_data.init_token(username=user, password=pwd, base_url=base_url)
    else:
        panda_data.init_token(username=user, password=pwd)
    return panda_data


def _is_rate_limit_error(exc: Exception) -> bool:
    """PandaData 500010 = 每分钟请求次数超限。"""
    msg = str(exc)
    return "500010" in msg or "每分钟请求次数超限" in msg


def _call_with_rate_limit_retry(fn, *, label: str, max_retries: int = 3, sleep_seconds: int = 35):
    """对 PandaData 调用包重试：仅在 500010（QPM 超限）时 sleep 后重试。
    其它错误立即抛。重试 max_retries 次仍超限则抛最后一次错。"""
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            if _is_rate_limit_error(exc) and attempt < max_retries:
                print(f"  [info] {label} 500010 限频，sleep {sleep_seconds}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(sleep_seconds)
                continue
            raise


def _fetch_daily_once(pd_api: Any, start: str, end: str, indicator: str = "") -> pd.DataFrame:
    # panda_data >=0.0.9 接口文档命名：get_stock_daily（A股日线 OHLC + limit_up/down + trade_status）
    # indicator: ""=全A / "000300"=沪深300 / "000852"=中证1000 / "399303"=国证2000
    return pd_api.get_stock_daily(
        start_date=_compact(start), end_date=_compact(end),
        symbol=[], fields=[], st=False, indicator=indicator,
    )


def load_daily(start: str, end: str, pd_api: Any | None = None,
               indicator: str = "") -> pd.DataFrame:
    """全市场日线，分段拉取 + 超限重试。
    indicator: ""=全A / "000300"=沪深300 / "000852"=中证1000 / "399303"=国证2000"""
    pd_api = pd_api or init_panda()
    frames, cur = [], pd.to_datetime(_compact(start)).date()
    end_d = pd.to_datetime(_compact(end)).date()
    while cur <= end_d:
        seg_end = min(cur + timedelta(days=DAILY_CHUNK_DAYS - 1), end_d)
        try:
            f = _fetch_daily_once(pd_api, cur.strftime("%Y%m%d"), seg_end.strftime("%Y%m%d"), indicator)
        except Exception as exc:  # noqa: BLE001
            if DAILY_CHUNK_DAYS > 90 and _is_retryable(exc):
                # 退化到 90 天再试
                sub = cur
                f_list = []
                while sub <= seg_end:
                    se = min(sub + timedelta(days=89), seg_end)
                    ff = _fetch_daily_once(pd_api, sub.strftime("%Y%m%d"), se.strftime("%Y%m%d"), indicator)
                    if ff is not None and not ff.empty:
                        f_list.append(ff)
                    sub = se + timedelta(days=1)
                f = pd.concat(f_list, ignore_index=True) if f_list else pd.DataFrame()
            else:
                raise
        if f is not None and not f.empty:
            frames.append(f)
        cur = seg_end + timedelta(days=1)
    if not frames:
        raise ValueError("get_stock_daily 未返回任何行情")
    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={"symbol": "ts_code", "date": "trade_date"})
    keep = ["trade_date", "ts_code", "name", "open", "close", "high", "low",
            "volume", "amount", "pre_close", "limit_up", "limit_down", "trade_status"]
    for c in keep:
        if c not in df.columns:
            df[c] = np.nan
    df = df[keep].copy()
    df["trade_date"] = _norm_date(df["trade_date"])
    for c in ["open", "close", "high", "low", "volume", "amount", "pre_close", "limit_up", "limit_down"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["trade_status"] = pd.to_numeric(df["trade_status"], errors="coerce").fillna(0).astype(int)
    df = df.dropna(subset=["trade_date", "ts_code", "close"])
    return df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def load_concepts(asof: str | None = None, pd_api: Any | None = None,
                  max_concepts: int = 200) -> Optional[pd.DataFrame]:
    """概念成分（题材龙头识别用）。
    策略：先用 get_concept_list 拿概念列表，再按概念逐个拉成分（避开"全量"600003 限额）。
    保留 in_date，下游按 in_date<=signal_date 做 PIT 过滤。失败返回 None。"""
    try:
        pd_api = pd_api or init_panda()
        # 1) 拉概念列表
        clist = pd_api.get_concept_list()
        if clist is None or clist.empty:
            return None
        concepts = clist["name"].dropna().unique().tolist()[:max_concepts]
        print(f"  [info] 遍历拉取 {len(concepts)} 个概念...")
        frames = []
        for i, name in enumerate(concepts):
            try:
                df = pd_api.get_concept_constituents(
                    concept=name,
                    date=_compact(asof) if asof else "",
                    concept_stock="",
                    fields=["concept", "concept_stock", "date"],
                )
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception:
                continue
            if (i + 1) % 50 == 0:
                print(f"  [info]   ...已拉 {i+1}/{len(concepts)} 个概念")
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True).rename(columns={"concept_stock": "ts_code", "date": "in_date"})
        df = df[["concept", "ts_code", "in_date"]].dropna(subset=["concept", "ts_code"]).drop_duplicates()
        print(f"  [info] 概念成分总计: {len(df)} 条, {df['concept'].nunique()} 个概念, {df['ts_code'].nunique()} 只标的")
        return df
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] 概念数据不可用，concept_leader 退化为市场口径: {exc}")
        return None


def load_lhb(start: str, end: str, pd_api: Any | None = None,
             chunk_days: int = 31) -> Optional[pd.DataFrame]:
    """龙虎榜买方明细（游资共振）。按 31 天分段拉取，规避单次过大被限。失败返回 None。"""
    try:
        pd_api = pd_api or init_panda()
        cur = pd.to_datetime(_compact(start)).date()
        end_d = pd.to_datetime(_compact(end)).date()
        frames = []
        seg_count = 0
        while cur <= end_d:
            seg_end = min(cur + timedelta(days=chunk_days - 1), end_d)
            try:
                df_seg = _call_with_rate_limit_retry(
                    lambda: pd_api.get_lhb_detail(
                        symbol=None,
                        start_date=cur.strftime("%Y%m%d"),
                        end_date=seg_end.strftime("%Y%m%d"),
                        side="buy", fields=[],
                    ),
                    label=f"lhb {cur}~{seg_end}",
                )
                if df_seg is not None and not df_seg.empty:
                    frames.append(df_seg)
            except Exception as e:
                print(f"  [warn] lhb 段 {cur}~{seg_end} 失败: {str(e)[:80]}")
            seg_count += 1
            if seg_count % 10 == 0:
                print(f"  [info]   ...lhb 已拉 {seg_count} 段")
            cur = seg_end + timedelta(days=1)
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True).rename(columns={"symbol": "ts_code"})
        df["date"] = _norm_date(df["date"])
        for c in ["b_value", "s_value"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        print(f"  [info] 龙虎榜: {len(df)} 条买方明细, {df['ts_code'].nunique()} 只标的")
        return df
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] 龙虎榜不可用，lhb 子项=0: {exc}")
        return None


def load_mktcap(start: str, end: str, symbols: list | None = None, pd_api: Any | None = None) -> Optional[pd.DataFrame]:
    """get_factor 取 market_cap/turnover（流通市值反推 + 封板强度代理）。
    自动按 3 年分段（接口硬限 5 年），失败返回 None。"""
    try:
        pd_api = pd_api or init_panda()
        cur = pd.to_datetime(_compact(start)).date()
        end_d = pd.to_datetime(_compact(end)).date()
        frames = []
        seg_years = 3
        while cur <= end_d:
            seg_end = min(cur.replace(year=cur.year + seg_years) - timedelta(days=1), end_d) \
                if cur.year + seg_years <= 9999 else end_d
            try:
                df_seg = _call_with_rate_limit_retry(
                    lambda: pd_api.get_factor(
                        symbol=symbols or "",
                        start_date=cur.strftime("%Y%m%d"),
                        end_date=seg_end.strftime("%Y%m%d"),
                        factors=["market_cap", "turnover", "close"], type="stock",
                    ),
                    label=f"get_factor {cur}~{seg_end}",
                )
                if df_seg is not None and not df_seg.empty:
                    frames.append(df_seg)
            except Exception as e:
                print(f"  [warn] get_factor 段 {cur}~{seg_end} 失败: {str(e)[:80]}")
            cur = seg_end + timedelta(days=1)
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True)
        df = df.rename(columns={"symbol": "ts_code", "date": "trade_date"})
        df["trade_date"] = _norm_date(df["trade_date"])
        for c in ["market_cap", "turnover"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        cols = [c for c in ["trade_date", "ts_code", "market_cap", "turnover"] if c in df.columns]
        df = df[cols].drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
        print(f"  [info] get_factor: {len(df)} 条 market_cap/turnover, {df['ts_code'].nunique()} 只标的")
        return df
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] get_factor 不可用，seal_strength 用 -成交额zscore 代理: {exc}")
        return None


def load_minute(daily_streak: pd.DataFrame, pd_api: Any | None = None,
                only_limit_days: bool = True) -> Optional[pd.DataFrame]:
    """对候选股的涨停日拉 1m 分钟线（流量优化版）。

    策略：
      - only_limit_days=True（默认）：只对 (ts_code, trade_date) where is_limit_up_close=True 的
        股票-日 组合拉分钟线。3 年候选只剩约 ~10000 个组合，每个 0.5-1s。
      - only_limit_days=False：候选股所有日期都拉（流量大 50 倍）。

    失败返回 None（factor 优雅降级到日线近似）。
    """
    try:
        pd_api = pd_api or init_panda()
        if not hasattr(pd_api, "get_stock_min"):
            print("  [warn] SDK 无 get_stock_min 接口，early_seal/blow_up 用日线近似")
            return None

        # 仅拉"涨停日"的分钟线
        ref = daily_streak[daily_streak["is_limit_up_close"]].copy() if only_limit_days else daily_streak
        if ref.empty:
            return None

        # 按 (ts_code, trade_date) 拉，每条独立 1 天的分钟线
        pairs = ref[["ts_code", "trade_date"]].drop_duplicates()
        print(f"  [info] 分钟线: 准备对 {len(pairs)} 个 (股票×日) 组合拉 1m 数据...")
        frames = []
        n_done = 0
        for _, row in pairs.iterrows():
            ts_code = row["ts_code"]
            d = pd.Timestamp(row["trade_date"]).strftime("%Y%m%d")
            try:
                f = pd_api.get_stock_min(
                    symbol=ts_code,
                    start_date=d, end_date=d,
                    fields=["symbol", "date", "datetime", "high", "close", "volume", "amount"],
                    frequency="1m",
                )
                if f is not None and not f.empty:
                    frames.append(f)
            except Exception:
                continue
            n_done += 1
            if n_done % 500 == 0:
                print(f"  [info]   ...分钟线已拉 {n_done}/{len(pairs)}")

        if not frames:
            print("  [warn] 分钟线拉取全失败，回退到日线近似")
            return None

        df = pd.concat(frames, ignore_index=True).rename(columns={"symbol": "ts_code", "date": "trade_date"})
        df["trade_date"] = _norm_date(df["trade_date"])
        # minute_idx: 当日序号 1..~240
        df = df.sort_values(["ts_code", "trade_date", "datetime"]).reset_index(drop=True)
        df["minute_idx"] = df.groupby(["ts_code", "trade_date"]).cumcount() + 1
        for c in ["high", "close", "volume", "amount"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        print(f"  [info] 分钟线: {len(df)} 条 / {df['ts_code'].nunique()} 标的 / {df['trade_date'].nunique()} 交易日")
        return df
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] 分钟线不可用，early_seal/blow_up 用日线近似: {exc}")
        return None


# ============================================================
# 股票池过滤
# ============================================================
def filter_universe(daily: pd.DataFrame) -> pd.DataFrame:
    code = daily["ts_code"].astype(str)
    is_sh_main = code.str.match(r"^(600|601|603|605)\d{3}\.SH$")
    is_sz_main = code.str.match(r"^(000|001|002|003)\d{3}\.SZ$")  # 含中小板并入后的深主板 002/003
    is_cyb = code.str.match(r"^(300|301)\d{3}\.SZ$")
    keep = is_sh_main | is_sz_main | is_cyb            # 排除 688 科创板、.BJ 北交所
    out = daily[keep].copy()
    if "name" in out.columns:
        name = out["name"].fillna("").astype(str)
        out = out[~name.str.contains("ST", case=False) & ~name.str.contains(r"\*")]
    return out.reset_index(drop=True)


# ============================================================
# 连板状态机 + 日线特征
# ============================================================
def build_streak(daily: pd.DataFrame, cfg: dict = DEFAULT_CONFIG) -> pd.DataFrame:
    df = daily.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = df.groupby("ts_code", sort=False)
    tol = cfg["limit_tol"]

    # 有效涨停价：优先接口 limit_up，缺失用 pre_close*1.1 兜底
    pre = df["pre_close"].where(df["pre_close"].notna(), g["close"].shift(1))
    # 涨停价缺失兜底：创业板(300/301) 20%、主板 10%，并按 0.01 取整（贴合交易所口径）
    rate = np.where(df["ts_code"].astype(str).str.match(r"^(300|301)\d{3}\.SZ$"), 1.20, 1.10)
    fallback = (pre * rate).round(2)
    df["eff_limit_up"] = df["limit_up"].where(df["limit_up"].gt(0), fallback)

    df["is_tradable"] = df["trade_status"].eq(0)
    df["is_limit_up_close"] = (
        df["is_tradable"] & df["close"].notna() & df["eff_limit_up"].notna()
        & df["close"].ge(df["eff_limit_up"] * tol)
    )
    # 连板只在交易日序列上累加：停牌日不算断板（A股口径），复牌后接续上一个交易日的板数
    trad = df[df["is_tradable"]]
    trad_streak = trad.groupby("ts_code")["is_limit_up_close"].transform(_consecutive_true_streak)
    df["limit_up_streak"] = trad_streak.reindex(df.index)
    df["limit_up_streak"] = df.groupby("ts_code")["limit_up_streak"].ffill().fillna(0).astype(int)

    # 一字板（次日不可买判定也复用此口径）：开盘与最低都贴板
    df["is_one_word"] = (
        df["open"].ge(df["eff_limit_up"] * tol) & df["low"].ge(df["eff_limit_up"] * tol)
        & df["is_limit_up_close"]
    )

    # 炸板代理（日线）：涨停收盘日内从涨停回落幅度 = (limit_up - low)/limit_up，越大越易炸
    df["blow_up_proxy"] = np.where(
        df["is_limit_up_close"],
        ((df["eff_limit_up"] - df["low"]) / df["eff_limit_up"]).clip(0, 0.25),
        0.0,
    )

    # 早封板近似分（日线）：一字/秒板→1，否则按 (收盘-开盘)/涨幅 粗估，无分钟时用
    rng = (df["eff_limit_up"] - pre).replace(0, np.nan)
    df["early_seal_score"] = np.where(
        df["is_one_word"], 1.0,
        np.where(df["is_limit_up_close"], ((df["open"] - pre) / rng).clip(0, 1).fillna(0.5), 0.0),
    )

    df["ret_5d"] = (df["close"] / g["close"].shift(5) - 1).fillna(0.0)
    df["ret_10d"] = (df["close"] / g["close"].shift(10) - 1).fillna(0.0)
    df["ret_20d"] = (df["close"] / g["close"].shift(20) - 1).fillna(0.0)

    # 60 日相对位置（0~1，越高越extended）
    max60 = g["high"].transform(lambda s: s.rolling(60, min_periods=20).max())
    min60 = g["low"].transform(lambda s: s.rolling(60, min_periods=20).min())
    df["position_60d"] = ((df["close"] - min60) / (max60 - min60).replace(0, np.nan)).clip(0, 1).fillna(0.5)
    return df


# ============================================================
# Layer 1：横截面强度打分
# ============================================================
def _concept_followers_rate(streak_map: pd.DataFrame,
                            concepts: Optional[pd.DataFrame]) -> pd.Series:
    """题材跟封率：候选所在概念中，当日 ≥1 板的股票占比（衡量梯队厚度）。
    无概念数据时返回 0。"""
    idx = streak_map.index
    if concepts is None or concepts.empty:
        return pd.Series(0.0, index=idx)
    c = concepts[["concept", "ts_code"]].copy()
    if "in_date" in concepts.columns:
        c["in_date"] = pd.to_datetime(concepts["in_date"], errors="coerce")
    # 每日每概念的"≥1 板比例"
    # 注意：这里 streak_map 是 ≥2 板的参照集，但需要看"全市场该概念有几只 ≥1 板"——需要原始 daily_streak
    # 折中：用参照集本身计算（≥2 板在该概念里的占比作为代理）
    ref = streak_map[["signal_date", "ts_code", "limit_up_streak"]].merge(
        c, on="ts_code", how="inner"
    )
    if "in_date" in ref.columns:
        ref = ref[ref["in_date"].isna() | (ref["in_date"] <= ref["signal_date"])]
    if ref.empty:
        return pd.Series(0.0, index=idx)
    # 每日每概念的样本数（≥2 板的概念跟封数）
    concept_size = ref.groupby(["signal_date", "concept"])["ts_code"].nunique().rename("n_in_concept")
    ref = ref.merge(concept_size.reset_index(), on=["signal_date", "concept"], how="left")
    # 一只票多概念取最大跟封数
    best = ref.groupby(["signal_date", "ts_code"])["n_in_concept"].max().reset_index()
    merged = streak_map.merge(best, on=["signal_date", "ts_code"], how="left")
    merged.index = streak_map.index   # 按索引对齐（L4），避免位置错位致 NaN
    out = merged["n_in_concept"].fillna(0).astype(float)
    # 归一化：log(1 + n) / log(20) 让 n=1 → 0.23, n=5 → 0.6, n=20 → 1.0
    return (np.log1p(out) / np.log(20.0)).reindex(idx).fillna(0.0)


def _concept_leader_score(cand_keys: pd.DataFrame, streak_map: pd.DataFrame,
                          concepts: Optional[pd.DataFrame]) -> pd.Series:
    """题材龙头身份：在所属概念内 streak 是否最高。无概念→市场口径 streak/市场max。"""
    idx = cand_keys.index
    if concepts is None or concepts.empty:
        mx = streak_map.groupby("signal_date")["limit_up_streak"].transform("max").replace(0, np.nan)
        return (streak_map["limit_up_streak"] / mx).reindex(idx).fillna(0.0).clip(0, 1)

    # ref 集（≥2板）按概念求每日每概念 max streak
    cols = ["concept", "ts_code"] + (["in_date"] if "in_date" in concepts.columns else [])
    c = concepts[cols].copy()
    if "in_date" in c.columns:
        c["in_date"] = pd.to_datetime(c["in_date"], errors="coerce")
    ref = streak_map.merge(c, on="ts_code", how="inner")
    # PIT：仅认信号日当天已纳入该概念的成分，杜绝"未来才纳入"回填历史（未来函数）
    if "in_date" in ref.columns:
        ref = ref[ref["in_date"].isna() | (ref["in_date"] <= ref["signal_date"])]
    if ref.empty:
        mx = streak_map.groupby("signal_date")["limit_up_streak"].transform("max").replace(0, np.nan)
        return (streak_map["limit_up_streak"] / mx).reindex(idx).fillna(0.0).clip(0, 1)
    cmax = ref.groupby(["signal_date", "concept"])["limit_up_streak"].transform("max")
    ref["leader_in_concept"] = (ref["limit_up_streak"] / cmax.replace(0, np.nan)).clip(0, 1)
    # 一只票可属多概念 → 取最强
    best = ref.groupby(["signal_date", "ts_code"])["leader_in_concept"].max().reset_index()
    merged = streak_map.merge(best, on=["signal_date", "ts_code"], how="left")
    merged.index = streak_map.index            # 按索引对齐，避免位置错位（L4）
    return merged["leader_in_concept"].reindex(idx).fillna(0.0).clip(0, 1)


def _lhb_signal(cand: pd.DataFrame, lhb: Optional[pd.DataFrame]) -> pd.Series:
    if lhb is None or lhb.empty:
        return pd.Series(0.0, index=cand.index)
    inst_kw = ["机构专用", "股通专用", "沪股通", "深股通", "港股通"]
    buy = lhb.copy()
    mask = ~buy["agency"].fillna("").astype(str).str.contains("|".join(inst_kw))
    buy = buy[mask]
    net = (buy.groupby(["ts_code", "date"])["b_value"].sum()
           - buy.groupby(["ts_code", "date"])["s_value"].sum()).reset_index(name="net_buy")
    net = net.rename(columns={"date": "signal_date"})
    out = cand.merge(net, on=["ts_code", "signal_date"], how="left")["net_buy"]
    return out.fillna(0.0)


def _early_seal_from_minute(cand: pd.DataFrame, minute: Optional[pd.DataFrame],
                            daily_streak: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """用分钟线覆盖 early_seal_score / blow_up_proxy（更精确）。返回带覆盖列的 cand。"""
    if minute is None or minute.empty:
        return cand
    lim = daily_streak[["trade_date", "ts_code", "eff_limit_up"]].rename(columns={"trade_date": "signal_date"})
    m = minute.rename(columns={"trade_date": "signal_date"}).merge(lim, on=["signal_date", "ts_code"], how="inner")
    if m.empty:
        return cand
    m["sealed"] = m["high"].ge(m["eff_limit_up"] * cfg["limit_tol"])
    # 首封分钟
    first = m[m["sealed"]].groupby(["signal_date", "ts_code"])["minute_idx"].min().reset_index(name="first_seal_min")
    first["early_seal_min"] = ((241 - first["first_seal_min"]) / 241).clip(0, 1)
    # 炸板次数：封→开 的切换数
    m = m.sort_values(["signal_date", "ts_code", "minute_idx"])
    m["unseal"] = (~m["sealed"]) & (m.groupby(["signal_date", "ts_code"])["sealed"].shift(1).fillna(False))
    blow = m.groupby(["signal_date", "ts_code"])["unseal"].sum().reset_index(name="blow_up_min")
    cand = cand.merge(first[["signal_date", "ts_code", "early_seal_min"]], on=["signal_date", "ts_code"], how="left")
    cand = cand.merge(blow, on=["signal_date", "ts_code"], how="left")
    cand["early_seal_score"] = cand["early_seal_min"].fillna(cand["early_seal_score"])
    cand["blow_up_proxy"] = np.where(cand["blow_up_min"].notna(),
                                     cand["blow_up_min"].fillna(0).clip(0, 10) / 10.0,
                                     cand["blow_up_proxy"])
    return cand.drop(columns=["early_seal_min", "blow_up_min"], errors="ignore")


def compute_market_state(daily_streak: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """大盘情绪标量子因子，每个 trade_date 一个值，所有候选股共享。

    返回字段:
      mkt_lu_count    : 当日全市场涨停家数
      mkt_blow_up_rate: 当日全市场炸板率 = 触及涨停未封死 / 触及涨停
      mkt_lu_z        : mkt_lu_count 的 window 日 z-score（>0 = 游资活跃）
      mkt_blow_up_z   : mkt_blow_up_rate 的 window 日 z-score（>0 = 抱团瓦解）

    用法: merge 到 ref（按 trade_date / signal_date）。
    """
    tol = 0.999
    g = daily_streak.groupby("trade_date")
    lu_count = g["is_limit_up_close"].sum().rename("mkt_lu_count")

    def _blow_up_rate(sub):
        touched = ((sub["high"] >= sub["eff_limit_up"] * tol)).sum()
        if touched == 0:
            return 0.0
        not_sealed = ((sub["high"] >= sub["eff_limit_up"] * tol) & ~sub["is_limit_up_close"]).sum()
        return float(not_sealed) / float(touched)

    blow_rate = g.apply(_blow_up_rate).rename("mkt_blow_up_rate")
    out = pd.concat([lu_count, blow_rate], axis=1).sort_index()

    # 60 日 rolling z-score（含当日，z 截尾至 [-3, 3]）
    out["mkt_lu_z"] = ((out["mkt_lu_count"] - out["mkt_lu_count"].rolling(window, min_periods=20).mean())
                       / out["mkt_lu_count"].rolling(window, min_periods=20).std().replace(0, np.nan)).clip(-3, 3)
    out["mkt_blow_up_z"] = ((out["mkt_blow_up_rate"] - out["mkt_blow_up_rate"].rolling(window, min_periods=20).mean())
                            / out["mkt_blow_up_rate"].rolling(window, min_periods=20).std().replace(0, np.nan)).clip(-3, 3)
    return out.reset_index()[["trade_date", "mkt_lu_count", "mkt_blow_up_rate", "mkt_lu_z", "mkt_blow_up_z"]]


def compute_layer1(daily_streak: pd.DataFrame, concepts=None, lhb=None, minute=None,
                   mktcap=None, cfg: dict = DEFAULT_CONFIG) -> pd.DataFrame:
    """A3 横截面打分（唯一一层）。"""
    # 大盘情绪标量（基于全 A daily_streak 算，跨日 z-score）
    mkt_state = compute_market_state(daily_streak, window=cfg.get("mkt_state_window", 60))

    df = daily_streak.rename(columns={"trade_date": "signal_date"}).copy()

    # 参照集：当日 ≥ref_min_streak 板（缓解候选池过窄，z 在更宽集合上算）
    ref = df[df["limit_up_streak"] >= cfg["ref_min_streak"]].copy()
    if ref.empty:
        return pd.DataFrame()

    # 把大盘情绪 broadcast 到每个候选股（同一日所有候选拿到同一对 z）
    ref = ref.merge(
        mkt_state.rename(columns={"trade_date": "signal_date"})[["signal_date", "mkt_lu_z", "mkt_blow_up_z", "mkt_lu_count", "mkt_blow_up_rate"]],
        on="signal_date", how="left",
    )

    # 封板强度代理：优先 -turnover；无 mktcap 用 -成交额横截面（当日 ref 集）
    if mktcap is not None and not mktcap.empty:
        mc = mktcap.rename(columns={"trade_date": "signal_date"})
        ref = ref.merge(mc[["signal_date", "ts_code", "turnover"]], on=["signal_date", "ts_code"], how="left")
        ref["seal_strength_proxy"] = -ref["turnover"].fillna(ref["turnover"].median())
    else:
        ref["seal_strength_proxy"] = -ref["amount"].fillna(ref["amount"].median())

    # 龙头身份 / 龙虎榜 / 题材跟封率
    ref["concept_leader_score"] = _concept_leader_score(ref, ref, concepts)
    ref["lhb_hotmoney"] = _lhb_signal(ref, lhb)
    ref["concept_followers_rate"] = _concept_followers_rate(ref, concepts)

    # 分钟线覆盖 early_seal / blow_up（若有）
    ref = _early_seal_from_minute(ref, minute, daily_streak, cfg)

    # 板高评分：当前用线性 z（IS 拟合曲线在 7-13 板因样本不足噪音过大，先不用）
    # 后续样本量积累后可启用 streak_band_curve = _load_streak_curve()
    ref["streak_band_score"] = _xs_zscore(
        ref["limit_up_streak"].astype(float), ref["signal_date"]
    ).fillna(0.0)

    # —— 横截面 z-score（沿股票轴，按 signal_date 分组）——
    by = ref["signal_date"]
    ref["leader_z"] = _xs_zscore(ref["concept_leader_score"], by)
    ref["early_seal_z"] = _xs_zscore(ref["early_seal_score"], by)
    ref["seal_strength_z"] = _xs_zscore(ref["seal_strength_proxy"], by)
    ref["blow_up_z"] = _xs_zscore(ref["blow_up_proxy"], by)
    ref["position_z"] = _xs_zscore(ref["position_60d"], by)
    ref["lhb_z"] = _xs_zscore(ref["lhb_hotmoney"], by)
    # 题材跟封率（同概念跟封多 = 梯队厚）
    if "concept_followers_rate" not in ref.columns:
        ref["concept_followers_rate"] = 0.0
    ref["concept_followers_z"] = _xs_zscore(ref["concept_followers_rate"], by)

    # 大盘情绪是当日标量（每股相同），直接用 z（已是 0 均值/单位 std 的跨日归一），无需横截面 z
    ref["mkt_lu_z"] = ref["mkt_lu_z"].fillna(0)
    ref["mkt_blow_up_z"] = ref["mkt_blow_up_z"].fillna(0)

    w = cfg["weights"]
    ref["base_score"] = (
        w.get("streak_band", w.get("streak", 0.20)) * ref["streak_band_score"]
        + w["leader"] * ref["leader_z"].fillna(0)
        + w["early_seal"] * ref["early_seal_z"].fillna(0)
        + w["seal_strength"] * ref["seal_strength_z"].fillna(0)
        + w["blow_up"] * ref["blow_up_z"].fillna(0)
        + w["position"] * ref["position_z"].fillna(0)
        + w["lhb"] * ref["lhb_z"].fillna(0)
        + w.get("concept_followers", 0.0) * ref["concept_followers_z"].fillna(0)
        + w.get("mkt_lu", 0.0) * ref["mkt_lu_z"]
        + w.get("mkt_blow_up", 0.0) * ref["mkt_blow_up_z"]
    )

    # 只保留 ≥min_streak 候选，在候选内做排名
    cand = ref[ref["limit_up_streak"] >= cfg["min_streak"]].copy()
    if cand.empty:
        return cand

    # 因子值 = base_score 原值（绝对值，不是百分位）
    cand["factor_value"] = cand["base_score"]

    # 候选池内排名
    cand["rank"] = cand.groupby("signal_date")["base_score"].rank(
        ascending=False, method="first"
    ).astype(int)

    # 每日 top-N 收敛：只保留 rank ≤ max_signals_per_day
    max_n = cfg.get("max_signals_per_day", 3)
    cand = cand[cand["rank"] <= max_n].copy()

    # score = base_score 经 sigmoid 映射到 0-100（绝对值，跨日可比）
    # 设定校准：base_score 在 [-1, +1] 对应 score [10, 90]，[-2,+2] 对应 [2, 98]
    cand["score"] = (100.0 / (1.0 + np.exp(-2.0 * cand["base_score"]))).round(2)

    keep = ["signal_date", "ts_code", "name", "factor_value", "score", "rank",
            "limit_up_streak", "streak_band_score",
            "concept_leader_score", "concept_followers_rate",
            "early_seal_score", "seal_strength_proxy",
            "blow_up_proxy", "position_60d", "lhb_hotmoney",
            "mkt_lu_count", "mkt_blow_up_rate", "mkt_lu_z", "mkt_blow_up_z",
            "is_one_word", "eff_limit_up", "close", "open"]
    keep = [c for c in keep if c in cand.columns]
    return cand[keep].reset_index(drop=True)


# ============================================================
# 组装信号
# ============================================================
def _next_trade_date_map(all_dates: pd.Series) -> dict:
    d = sorted(pd.Series(all_dates).dropna().unique())
    return {d[i]: d[i + 1] for i in range(len(d) - 1)}


def assemble_signals(layer1: pd.DataFrame, cfg: dict = DEFAULT_CONFIG,
                     all_dates: pd.Series | None = None) -> pd.DataFrame:
    """从 Layer 1 score 直接生成 signal（不再使用状态机）。"""
    if layer1.empty:
        return layer1
    df = layer1.copy()

    # trade_exec_date = 下一交易日
    if all_dates is not None:
        nmap = _next_trade_date_map(all_dates)
        df["trade_exec_date"] = df["signal_date"].map(nmap)
    else:
        df["trade_exec_date"] = pd.NaT

    df["confidence"] = np.where(
        df["score"] >= cfg["buy_th"], df["score"] / 100.0,
        np.where(df["score"] >= cfg["watch_th"], df["score"] / 100.0 * 0.7, 0.3)
    )

    def _sig(r):
        if bool(r.get("is_one_word", False)):
            return "unfillable"
        if r["score"] >= cfg["buy_th"]:
            return "buy"
        if r["score"] >= cfg["watch_th"]:
            return "watch"
        return "hold"

    df["signal"] = df.apply(_sig, axis=1)
    return df


def add_metadata(panel: pd.DataFrame, cfg: dict = DEFAULT_CONFIG) -> pd.DataFrame:
    df = panel.copy()
    now = datetime.now()
    df["factor_id"] = FACTOR_ID
    df["factor_name"] = FACTOR_NAME
    df["asset_type"] = ASSET_TYPE
    df["data_version"] = DEFAULT_DATA_VERSION
    df["update_time"] = now.isoformat()
    # 兼容生产规则 §9：主键字段 trade_date 即为 signal_date 的别名
    if "signal_date" in df.columns and "trade_date" not in df.columns:
        df["trade_date"] = df["signal_date"]
    return df


def check_quality(panel: pd.DataFrame) -> list[str]:
    errs = []
    if panel.empty:
        return ["结果为空（可能区间内无 ≥3 板候选）"]
    dup = panel.duplicated(subset=["signal_date", "ts_code", "factor_id"], keep=False)
    if dup.any():
        errs.append(f"主键重复 {int(dup.sum())} 条")
    for c in ["signal_date", "ts_code", "factor_id", "factor_value", "score", "signal"]:
        if c in panel.columns and panel[c].isnull().any():
            errs.append(f"'{c}' 存在空值")
    if "score" in panel and (panel["score"].min() < 0 or panel["score"].max() > 100):
        errs.append(f"score 越界 [{panel['score'].min():.1f},{panel['score'].max():.1f}]")
    if "signal" in panel:
        bad = set(panel["signal"].unique()) - {"buy", "watch", "hold", "unfillable"}
        if bad:
            errs.append(f"非法 signal: {bad}")
    return errs


# ============================================================
# 全流程编排
# ============================================================
def run_factor(start: str, end: str, cfg: dict = DEFAULT_CONFIG, with_minute: bool = False,
               indicator: str = "") -> pd.DataFrame:
    pd_api = init_panda()
    pool_name = {"": "全A", "000300": "沪深300", "000852": "中证1000", "399303": "国证2000"}.get(indicator, indicator or "全A")
    print(f"[1/5] 拉取日线 (股票池={pool_name}) ...")
    daily = filter_universe(load_daily(start, end, pd_api, indicator=indicator))
    print(f"      universe: {daily['ts_code'].nunique()} 标的, {daily['trade_date'].nunique()} 交易日")

    print("[2/5] 连板状态机 ...")
    streak = build_streak(daily, cfg)
    n_cand = (streak["limit_up_streak"] >= cfg["min_streak"]).sum()
    print(f"      ≥{cfg['min_streak']}板候选样本: {int(n_cand)} 条")

    print("[3/5] 辅助数据（概念/龙虎榜/市值/分钟）...")
    concepts = load_concepts(asof=end, pd_api=pd_api)
    lhb = load_lhb(start, end, pd_api)
    mktcap = load_mktcap(start, end, pd_api=pd_api)
    minute = None
    if with_minute:
        # 只对 ≥ref_min_streak 板的 streak 子集 拉分钟线（只拉涨停日，流量优化）
        minute_input = streak[streak["limit_up_streak"] >= cfg["ref_min_streak"]]
        minute = load_minute(minute_input, pd_api=pd_api, only_limit_days=True)

    print("[4/6] Layer1 横截面打分 + 组装信号 ...")
    layer1 = compute_layer1(streak, concepts, lhb, minute, mktcap, cfg=cfg)
    panel = assemble_signals(layer1, cfg, all_dates=daily["trade_date"])

    # 【重要】在 daily 已有的情况下，一次性算好 forward_return 并缓存进 parquet
    # 这样 backtest / strategy 不需要再拉行情（省流量）
    print("[5/6] 算 forward_return 缓存进 parquet ...")
    forward = compute_forward_returns(daily)
    panel = panel.merge(forward, left_on=["signal_date", "ts_code"],
                        right_on=["trade_date", "ts_code"], how="left", suffixes=("", "_y"))
    panel = panel.drop(columns=["trade_date_y"], errors="ignore")
    if "trade_date" in panel.columns and "signal_date" in panel.columns:
        # signal_date 才是主键，trade_date 可能是 merge 带来的；统一保留 signal_date
        panel = panel.drop(columns=["trade_date"], errors="ignore")
    print(f"      forward_return 非空率: {panel['forward_return'].notna().mean():.1%}")
    print(f"      fillable 比例: {panel['fillable'].mean():.1%}")

    # 用 fillable=False 精确覆写 signal=unfillable（fillable 来自 T+1 实际开盘价对比涨停价，
    # 比 is_one_word 的 T 日代理判断更精确；与文档"unfillable=T+1 一字板/开盘涨停/停牌"对齐）
    if "fillable" in panel.columns:
        unfillable_mask = panel["fillable"] == False  # noqa: E712 - 显式比较过滤 NaN
        n_overwrite = int(unfillable_mask.sum() - (panel["signal"] == "unfillable").sum())
        panel.loc[unfillable_mask, "signal"] = "unfillable"
        if n_overwrite > 0:
            print(f"      fillable=False 覆写 signal -> unfillable: 新增 {n_overwrite} 条")

    panel = add_metadata(panel, cfg)

    print("[6/6] 质量检查 ...")
    errs = check_quality(panel)
    if errs:
        print("  [FAIL] " + "; ".join(errs))
    else:
        print("  [PASS] 质量检查通过")
    return panel


def compute_forward_returns(quotes: pd.DataFrame) -> pd.DataFrame:
    """因子在 t 日形成；入场 t+1 open，出场 t+2 vwap(amount/volume)。
    返回 (trade_date=t, ts_code) → forward_return / fillable / next_open / vwap_t2。
    与 backtest.py 的 build_forward_returns 完全一致，保持单一事实来源。
    """
    df = quotes.sort_values(["ts_code", "trade_date"]).copy()
    g = df.groupby("ts_code", sort=False)

    df["next_open"] = g["open"].shift(-1)
    df["next_high"] = g["high"].shift(-1)
    df["next_low"] = g["low"].shift(-1)
    df["next_trade_status"] = g["trade_status"].shift(-1)
    df["next2_amount"] = g["amount"].shift(-2)
    df["next2_volume"] = g["volume"].shift(-2)
    df["next2_close"] = g["close"].shift(-2)

    rate = np.where(df["ts_code"].astype(str).str.match(r"^(300|301)\d{3}\.SZ$"), 1.20, 1.10)
    df["eff_limit_up_next"] = (df["close"] * rate).round(2)

    tol = 0.999
    one_word_next = (
        df["next_open"].ge(df["eff_limit_up_next"] * tol)
        & df["next_low"].ge(df["eff_limit_up_next"] * tol)
        & df["next_high"].ge(df["eff_limit_up_next"] * tol)
    )
    open_at_limit_next = df["next_open"].ge(df["eff_limit_up_next"] * tol)
    halt_next = df["next_trade_status"].fillna(0).ne(0)
    df["fillable"] = ~(one_word_next | open_at_limit_next | halt_next) & df["next_open"].gt(0)

    df["vwap_t2"] = (df["next2_amount"] / df["next2_volume"]).where(
        df["next2_volume"].gt(0), df["next2_close"]
    )
    df["forward_return"] = df["vwap_t2"] / df["next_open"] - 1.0

    out = df[["trade_date", "ts_code", "forward_return", "fillable", "next_open", "vwap_t2"]]
    return out.dropna(subset=["trade_date", "ts_code"])


STANDARD_COLS = [
    # 主键 + 标识（trade_date 用作生产规则 §9 标准主键，与 signal_date 同义）
    "trade_date", "signal_date", "trade_exec_date",
    "asset_type", "ts_code", "name",
    "factor_id", "factor_name", "factor_value", "score", "rank", "signal", "confidence",
    # Layer 1 子项
    "limit_up_streak", "streak_band_score",
    "concept_leader_score", "concept_followers_rate",
    "early_seal_score", "seal_strength_proxy",
    "blow_up_proxy", "position_60d", "lhb_hotmoney", "is_one_word",
    # 大盘情绪标量子因子（当日所有候选共享）
    "mkt_lu_count", "mkt_blow_up_rate", "mkt_lu_z", "mkt_blow_up_z",
    # forward_return 缓存（backtest 直接读，不再重拉行情）
    "forward_return", "fillable", "next_open", "vwap_t2",
    "data_version", "update_time",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Alpha-A3 连板龙头接力因子")
    ap.add_argument("--start", default="20230619")
    ap.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--with-minute", action="store_true", help="对候选拉分钟线提取首封/炸板")
    ap.add_argument("--indicator", default="", choices=["", "000300", "000852", "399303"],
                    help='股票池：""=全A / 000300=沪深300 / 000852=中证1000 / 399303=国证2000')
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--weights", default=str(DEFAULT_WEIGHTS_JSON),
                    help="校准权重 JSON 路径（见 calibrate_weights.py）；不存在则用 DEFAULT_CONFIG")
    ap.add_argument("--no-calibrated", action="store_true",
                    help="强制忽略 --weights，使用 DEFAULT_CONFIG 默认权重")
    args = ap.parse_args()

    cfg = DEFAULT_CONFIG
    if not args.no_calibrated:
        w_override = load_calibrated_weights(Path(args.weights))
        if w_override:
            cfg = apply_weights(DEFAULT_CONFIG, w_override)
            print(f"  [info] 使用校准权重: {args.weights}")
            print(f"  [info] 权重 = {cfg['weights']}")

    pool_name = {"": "全A", "000300": "沪深300", "000852": "中证1000", "399303": "国证2000"}.get(args.indicator, args.indicator)
    print("=" * 64)
    print(f"Alpha-A3 连板龙头接力 | {args.start} ~ {args.end} | 股票池={pool_name}")
    print("=" * 64)
    panel = run_factor(args.start, args.end, cfg, with_minute=args.with_minute,
                       indicator=args.indicator)
    if panel.empty:
        print("无候选结果，退出。")
        return
    out_cols = [c for c in STANDARD_COLS if c in panel.columns]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    panel[out_cols].to_parquet(args.out, index=False)
    print(f"\n已保存 {len(panel)} 条 → {args.out}")

    latest = panel[panel["signal_date"] == panel["signal_date"].max()]
    print(f"\n最新信号日 {panel['signal_date'].max().date()}")
    show = ["ts_code", "name", "limit_up_streak", "score", "signal", "confidence"]
    show = [c for c in show if c in latest.columns]
    print(latest.sort_values("score", ascending=False)[show].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
