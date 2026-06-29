#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpha-A3 HTML 可视化报告（v3 多维度展现版）
================================================================
设计原则：
- OOS（近 6 月）当主角，ALL/IS 当诊断
- 多维度：资金曲线 / 回撤 / 月度 / 板高 fwd_return / 信号类别 fwd_return
- 多组合矩阵：3 区间 × 2 组 × 2 成本
- 当前信号 + 子因子雷达
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ALPHA_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTOR_PARQUET = ALPHA_ROOT / "skill-alpha-a3-streak-leader-relay-production" / "database.parquet"
DEFAULT_FACTOR_JSON = ALPHA_ROOT / "skill-alpha-a3-streak-leader-relay-production" / "backtest_metrics.json"
DEFAULT_STRATEGY_JSON = ALPHA_ROOT / "skill-alpha-a3-streak-leader-relay-production" / "strategy_backtest_metrics.json"
DEFAULT_HTML = ALPHA_ROOT / "skill-alpha-a3-streak-leader-relay-production" / "backtest_report.html"


def safe_load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def fmt(v, suffix=""):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if isinstance(v, float):
        if abs(v) > 1000:
            return f"{v:,.0f}{suffix}"
        return f"{v:.4f}{suffix}"
    return f"{v}{suffix}"


def find_factor(reports, interval):
    """interval ∈ {'ALL', 'IS', 'OOS'}"""
    if not reports:
        return None
    for r in reports:
        if interval in r.get("label", ""):
            return r
    return None


def find_strategy(scenarios, interval, group, cost):
    """interval/group/cost 关键字匹配 strategy scenario"""
    if not scenarios:
        return None
    for s in scenarios:
        lbl = s.get("label", "")
        if interval in lbl and group in lbl and cost in lbl:
            return s
    return None


# ============================================================
# 因子研判：数据驱动的整体判断（放最顶部）
# ============================================================
def render_factor_review(factor_reports, strategy_scenarios, parquet_path) -> str:
    """生成基于实际数据的因子研判，rule-based 自适应。"""
    df = pd.read_parquet(parquet_path) if parquet_path.exists() else pd.DataFrame()
    oos_f = find_factor(factor_reports, "OOS") or {}
    is_f = find_factor(factor_reports, "IS") or {}
    all_f = find_factor(factor_reports, "ALL") or {}
    oos_pure = find_strategy(strategy_scenarios, "OOS", "纯A3", "标准")
    oos_pure_stress = find_strategy(strategy_scenarios, "OOS", "纯A3", "压力")
    oos_gate = find_strategy(strategy_scenarios, "OOS", "IC gate", "标准")

    oos_ic = oos_f.get("IC")
    oos_icir = oos_f.get("ICIR")
    is_ic = is_f.get("IC")
    oos_m = (oos_pure or {}).get("metrics", {})
    oos_arr = oos_m.get("ARR_pct")
    oos_win = oos_m.get("胜率")
    oos_sharpe = oos_m.get("夏普")
    oos_cum = oos_m.get("累计收益_pct")
    oos_arr_stress = (oos_pure_stress or {}).get("metrics", {}).get("ARR_pct")
    oos_gate_arr = (oos_gate or {}).get("metrics", {}).get("ARR_pct")

    # 从 parquet 算特征
    avg_breadth = float(df.groupby("trade_date")["ts_code"].count().mean()) if len(df) else 0.0
    n_days = int(df["trade_date"].nunique()) if len(df) else 0
    n_rows = len(df)
    fillable_rate = float(df["fillable"].mean()) if "fillable" in df.columns and df["fillable"].notna().any() else None
    one_word_rate = float(df["is_one_word"].mean()) if "is_one_word" in df.columns else None
    candidate_fwd_mean = float(df["forward_return"].mean()) if "forward_return" in df.columns and df["forward_return"].notna().any() else None
    buy_signal_count = int((df["signal"] == "buy").sum()) if "signal" in df.columns else 0

    # 月度趋势（OOS 最近 3 月）
    recent_months = []
    if oos_m.get("monthly_return"):
        recent_months = oos_m["monthly_return"][-3:]

    # 子因子健康度
    sub_health = []
    for col in ["concept_leader_score", "concept_followers_rate", "early_seal_score",
                "blow_up_proxy", "lhb_hotmoney"]:
        if col in df.columns:
            rate = df[col].notna().mean()
            sub_health.append((col, rate))

    # ===== 总评 (verdict) =====
    if oos_ic and oos_arr and oos_ic > 0.05 and oos_arr > 0 and oos_sharpe and oos_sharpe > 0.5:
        verdict_emoji = "✅"
        verdict_text = "近 6 月 OOS 区分力 + 正收益同时成立，可作为候选发现器使用"
        verdict_cls = "verdict-good"
    elif oos_ic and oos_ic > 0:
        verdict_emoji = "🟡"
        verdict_text = "OOS 有区分力但收益薄/不稳，建议作为候选名单不直接下单"
        verdict_cls = "verdict-warn"
    else:
        verdict_emoji = "🔴"
        verdict_text = "OOS 不达可投资门槛，观望"
        verdict_cls = "verdict-bad"

    # ===== 一句话总评 =====
    overall = (f"A3 是<strong>事件型 T+1 接力因子</strong>，从 A 股 ≥3 板候选池中识别值得接力的龙头。"
               f"过去 <strong>{n_days}</strong> 个交易日累计输出 <strong>{n_rows}</strong> 条信号，"
               f"日均横截面宽度 <strong>{avg_breadth:.2f}</strong>（每日候选 ~3 只），"
               f"其中 <strong>{buy_signal_count}</strong> 条 buy 信号。")

    # ===== 关键发现 =====
    findings = []

    # 1) OOS 因子预测力
    if oos_ic is not None and oos_icir is not None:
        if oos_icir > 0.10:
            kind = "good"
            extra = "达学界 ICIR&gt;0.10 的可投资门槛"
        elif oos_icir > 0.05:
            kind = "warn"
            extra = "接近可投资门槛 (0.05&lt;ICIR&lt;0.10) 但需谨慎"
        else:
            kind = "bad"
            extra = "ICIR 偏低，信噪比不足"
        findings.append({
            "kind": kind, "title": "OOS 因子预测力",
            "text": f"近 6 月 IC=<strong>{oos_ic:.4f}</strong>，ICIR=<strong>{oos_icir:.4f}</strong>。{extra}。"
        })

    # 2) IS / OOS 分化
    if oos_ic is not None and is_ic is not None:
        diff = oos_ic - is_ic
        if diff > 0.05:
            kind = "warn"
            extra = "OOS 显著强于 IS — 因子对游资活跃环境敏感，环境一变会失效"
        elif diff < -0.05:
            kind = "bad"
            extra = "OOS 弱于 IS，可能过拟合"
        else:
            kind = "good"
            extra = "IS/OOS 接近，因子稳定"
        findings.append({
            "kind": kind, "title": "IS / OOS 分化",
            "text": f"IS IC=<strong>{is_ic:.4f}</strong> vs OOS IC=<strong>{oos_ic:.4f}</strong>，差异 {diff:+.4f}。{extra}。"
        })

    # 3) 候选池负偏
    if candidate_fwd_mean is not None:
        v = candidate_fwd_mean * 100
        if candidate_fwd_mean < -0.005:
            kind = "bad"
            extra = "A 股 ≥3 板候选池呈现结构性次日均值负偏（均值回归压力），无脑跟进必败"
        elif candidate_fwd_mean < 0:
            kind = "warn"
            extra = "候选池整体微负，需要因子识别相对强者"
        else:
            kind = "good"
            extra = "候选池均值非负，跟进风险可控"
        findings.append({
            "kind": kind, "title": "候选池整体次日均值",
            "text": f"<strong>{v:+.3f}%</strong>（全样本所有 ≥3 板候选）。{extra}。"
        })

    # 4) 可成交率
    if fillable_rate is not None:
        rate = fillable_rate * 100
        kind = "good" if 0.55 < fillable_rate < 0.70 else "warn"
        findings.append({
            "kind": kind, "title": "T+1 可成交率",
            "text": f"<strong>{rate:.1f}%</strong>。剩下 {100-rate:.1f}% 是高度一字板/停牌，"
                    f"是高度龙头但接力不上。"
        })

    # 5) 成本敏感度
    if oos_arr is not None and oos_arr_stress is not None:
        cost_sensitive = oos_arr > 0 > oos_arr_stress
        findings.append({
            "kind": "warn" if cost_sensitive else "neutral",
            "title": "成本敏感度",
            "text": f"标准 0.10% ARR=<strong>{oos_arr:+.1f}%</strong> → 压力 0.15% ARR=<strong>{oos_arr_stress:+.1f}%</strong>。" +
                    ("⚠️ 0.05% 滑点会侵蚀部分收益,但仍可正向" if cost_sensitive
                     else "成本不敏感 — 万五佣金 + 印花税即可")
        })

    # 6) IC gate 增益
    if oos_arr is not None and oos_gate_arr is not None:
        gate_better = oos_gate_arr > oos_arr
        delta = oos_gate_arr - oos_arr
        findings.append({
            "kind": "good" if gate_better else "warn",
            "title": "IC gate 增益",
            "text": f"OOS 含 IC gate ARR=<strong>{oos_gate_arr:+.1f}%</strong> "
                    f"vs 纯 A3 ARR=<strong>{oos_arr:+.1f}%</strong>，差 {delta:+.1f}%。" +
                    (" gate 仍有正向贡献" if gate_better else
                     " gate 反而拖累 — 子因子已内化游资活跃度信息，gate 冗余")
        })

    # 7) 最近月度
    if recent_months:
        ms_str = " / ".join(f"<strong>{m}</strong> ({v:+.1f}%)" for m, v in recent_months)
        last_val = recent_months[-1][1]
        kind = "good" if last_val > 10 else "warn" if last_val > -10 else "bad"
        findings.append({
            "kind": kind, "title": "近 3 月走势",
            "text": f"{ms_str}。" + ("当前环境对 A3 友好" if last_val > 10 else
                                      "环境一般" if last_val > -10 else "环境不利，考虑暂停")
        })

    # ===== 实操建议 =====
    advice = []
    if oos_ic and oos_ic > 0.05:
        advice.append(("✅", "paper trading 跑 1-2 个月活样本验证真实表现"))
    if oos_arr_stress is not None and oos_arr is not None and oos_arr > 0 > oos_arr_stress:
        advice.append(("⚠️", "费率敏感 — 实盘双边需 &lt; 0.15%（万五佣金 + 印花税 + 滑点）"))
    if oos_gate_arr is not None and oos_arr is not None and oos_gate_arr < oos_arr:
        advice.append(("💡", "IC gate 当前是冗余约束，直接用纯 A3 + 人工选时即可"))
    advice.append(("📋", "每日只入 score≥80 且 fillable=True 的信号；rank=1 一字板要果断放弃"))
    advice.append(("🛑", "连续 2 个月 OOS 月度收益 &lt; -10% 时暂停策略 1 个月观察"))
    if avg_breadth < 5:
        advice.append(("⚠️", f"日均候选 {avg_breadth:.1f} 只过窄，建议同时叠 A2/A4 等其他事件因子分散"))

    # ===== 拼装 HTML =====
    finding_html = []
    for f in findings:
        finding_html.append(f'''
        <div class="finding finding-{f["kind"]}">
          <div class="finding-title">{f["title"]}</div>
          <div class="finding-text">{f["text"]}</div>
        </div>''')

    advice_html = []
    for icon, text in advice:
        advice_html.append(f'<li><span class="advice-icon">{icon}</span>{text}</li>')

    return f'''
    <section class="review-section">
      <h2>🎯 因子研判</h2>
      <div class="verdict {verdict_cls}">
        <div class="verdict-emoji">{verdict_emoji}</div>
        <div class="verdict-text">{verdict_text}</div>
      </div>
      <p class="overall">{overall}</p>
      <h3>📌 关键发现</h3>
      <div class="findings-grid">{"".join(finding_html)}</div>
      <h3>📋 实操建议</h3>
      <ul class="advice-list">{"".join(advice_html)}</ul>
    </section>
    '''


# ============================================================
# 最近 N 日交易明细：每天 top-3 + 实际次日收益
# ============================================================
def render_recent_days(parquet_path, n_days=20) -> str:
    if not parquet_path.exists():
        return ""
    df = pd.read_parquet(parquet_path)
    if "trade_date" not in df.columns:
        return ""
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    unique_dates = sorted(df["trade_date"].unique(), reverse=True)[:n_days]
    if not unique_dates:
        return ""
    df_recent = df[df["trade_date"].isin(unique_dates)].sort_values(["trade_date", "rank"],
                                                                    ascending=[False, True])

    parts = [f'<section><h2>📅 最近 {len(unique_dates)} 个交易日明细</h2>']
    parts.append('<p class="hint">每行 = 一个信号。日期降序，每天最多 3 个 rank。'
                 'forward_return = T+1 open 进 T+2 vwap 出 的实际毛收益。'
                 '一字板 unfillable 不计入当日组合 P&L。</p>')

    parts.append('<div class="recent-wrap"><table class="recent-table">')
    parts.append('<tr><th>日期</th><th>Rank</th><th>代码</th><th>名称</th>'
                 '<th>Signal</th><th>Score</th><th>板</th><th>题材龙</th>'
                 '<th>早封板</th><th>fillable</th>'
                 '<th>next_open</th><th>vwap T+2</th><th>forward_return</th></tr>')

    for _, r in df_recent.iterrows():
        d = pd.Timestamp(r["trade_date"]).strftime("%Y-%m-%d")
        sig = r.get("signal", "")
        sig_cls = {"buy": "sig-buy", "watch": "sig-watch", "hold": "sig-hold",
                   "unfillable": "sig-unfillable"}.get(sig, "")
        fwd = r.get("forward_return")
        if pd.notna(fwd):
            fwd_pct = float(fwd) * 100
            fwd_cls = "cell-pos" if fwd_pct > 0 else "cell-neg"
            fwd_disp = f'<span class="{fwd_cls}">{fwd_pct:+.2f}%</span>'
        else:
            fwd_disp = '<span style="color:#94a3b8">—</span>'

        fillable = r.get("fillable")
        fillable_disp = "✓" if fillable else "✗"
        fillable_cls = "color:#10b981" if fillable else "color:#94a3b8"

        next_open = r.get("next_open")
        next_open_disp = f"{float(next_open):.2f}" if pd.notna(next_open) else "—"
        vwap = r.get("vwap_t2")
        vwap_disp = f"{float(vwap):.2f}" if pd.notna(vwap) else "—"

        cl_score = r.get("concept_leader_score", 0)
        es_score = r.get("early_seal_score", 0)

        parts.append(f'''
        <tr>
          <td>{d}</td>
          <td><span class="rank-mini">#{int(r["rank"])}</span></td>
          <td class="ts-code">{r["ts_code"]}</td>
          <td><strong>{r["name"]}</strong></td>
          <td><span class="signal-mini {sig_cls}">{sig}</span></td>
          <td><strong>{float(r["score"]):.1f}</strong></td>
          <td>{int(r["limit_up_streak"])}</td>
          <td>{float(cl_score):.2f}</td>
          <td>{float(es_score):.2f}</td>
          <td style="{fillable_cls};font-weight:600">{fillable_disp}</td>
          <td>{next_open_disp}</td>
          <td>{vwap_disp}</td>
          <td>{fwd_disp}</td>
        </tr>''')

    parts.append('</table></div>')

    # ===== 当日组合 P&L 汇总 (每天一行) =====
    parts.append('<h3 style="margin-top:24px">📊 当日组合表现汇总</h3>')
    parts.append('<p class="hint">仅统计 fillable=True 的信号；buy 信号等权分仓的当日实际毛收益（未扣成本）</p>')
    parts.append('<table class="daily-pnl-table">')
    parts.append('<tr><th>日期</th><th>信号数</th><th>buy 数</th><th>fillable</th>'
                 '<th>当日 buy 均值</th><th>当日全部均值</th><th>胜率</th></tr>')

    for d in unique_dates:
        sub = df_recent[df_recent["trade_date"] == d]
        n_sig = len(sub)
        n_buy = int((sub["signal"] == "buy").sum())
        n_fill = int(sub["fillable"].sum()) if "fillable" in sub.columns else 0

        buy_fillable = sub[(sub["signal"] == "buy") & (sub["fillable"] == True)]
        all_fillable = sub[sub["fillable"] == True]

        buy_mean = buy_fillable["forward_return"].mean() if len(buy_fillable) and buy_fillable["forward_return"].notna().any() else None
        all_mean = all_fillable["forward_return"].mean() if len(all_fillable) and all_fillable["forward_return"].notna().any() else None
        win_rate = (all_fillable["forward_return"] > 0).mean() if len(all_fillable) and all_fillable["forward_return"].notna().any() else None

        def fmt_pct(v):
            if v is None or pd.isna(v):
                return '<span style="color:#94a3b8">—</span>'
            cls = "cell-pos" if v > 0 else "cell-neg"
            return f'<span class="{cls}">{v*100:+.2f}%</span>'

        win_disp = f"{win_rate*100:.0f}%" if win_rate is not None and not pd.isna(win_rate) else "—"

        parts.append(f'''
        <tr>
          <td><strong>{pd.Timestamp(d).strftime("%Y-%m-%d")}</strong></td>
          <td>{n_sig}</td>
          <td>{n_buy}</td>
          <td>{n_fill}</td>
          <td>{fmt_pct(buy_mean)}</td>
          <td>{fmt_pct(all_mean)}</td>
          <td>{win_disp}</td>
        </tr>''')
    parts.append('</table>')
    parts.append('</section>')
    return "\n".join(parts)


# ============================================================
# Hero：OOS 主角
# ============================================================
def render_hero(factor_reports, strategy_scenarios) -> str:
    oos_f = find_factor(factor_reports, "OOS")
    oos_pure = find_strategy(strategy_scenarios, "OOS", "纯A3", "标准")
    oos_gate = find_strategy(strategy_scenarios, "OOS", "IC gate", "标准")
    all_f = find_factor(factor_reports, "ALL")
    all_pure = find_strategy(strategy_scenarios, "ALL", "纯A3", "标准")

    # OOS 主卡（绿色突出）
    primary_cards = []
    if oos_f:
        primary_cards += [
            ("IC (OOS)", fmt(oos_f.get("IC")), "primary", "OOS 因子预测力"),
            ("ICIR (OOS)", fmt(oos_f.get("ICIR")), "primary", "目标 > 0.10"),
        ]
    if oos_pure:
        m = oos_pure.get("metrics", {})
        primary_cards += [
            ("ARR% (OOS, 纯A3, 0.10%)", fmt(m.get("ARR_pct"), "%"), "good", "近 6 月年化"),
            ("夏普 (OOS, 纯A3)", fmt(m.get("夏普")), "good", "目标 > 0.5"),
            ("累计% (OOS)", fmt(m.get("累计收益_pct"), "%"), "good", "近 6 月累计"),
            ("胜率 (OOS)", f"{m.get('胜率', 0)*100:.1f}%", "good", "盈利天数占比"),
            ("MDD (OOS)", fmt(m.get("MDD_pct"), "%"), "warn", "最大回撤"),
        ]
    if oos_gate:
        m = oos_gate.get("metrics", {})
        primary_cards += [
            ("ARR% (OOS, IC gate)", fmt(m.get("ARR_pct"), "%"), "muted", "对照组：含 IC gate"),
        ]

    # ALL 诊断卡（小字灰色）
    diag_cards = []
    if all_f:
        diag_cards += [
            ("IC (ALL)", fmt(all_f.get("IC"))),
            ("ICIR (ALL)", fmt(all_f.get("ICIR"))),
        ]
    if all_pure:
        m = all_pure.get("metrics", {})
        diag_cards += [
            ("ARR% (ALL)", fmt(m.get("ARR_pct"), "%")),
            ("MDD (ALL)", fmt(m.get("MDD_pct"), "%")),
            ("夏普 (ALL)", fmt(m.get("夏普"))),
        ]

    primary_html = []
    for title, value, kind, sub in primary_cards:
        primary_html.append(f'''
        <div class="metric-card metric-{kind}">
          <div class="metric-title">{title}</div>
          <div class="metric-value">{value}</div>
          <div class="metric-sub">{sub}</div>
        </div>''')

    diag_html = []
    for title, value in diag_cards:
        diag_html.append(f'<div class="diag-pill"><span>{title}</span><strong>{value}</strong></div>')

    return f'''
    <div class="hero-banner">
      <div class="hero-label">🌟 OOS 近 6 个月样本外 · 推荐看这里</div>
      <div class="metric-grid">{"".join(primary_html)}</div>
    </div>

    <details class="diag-bar">
      <summary>📉 诊断维度：ALL 全样本（含因子失效期）— 点击展开</summary>
      <p class="hint" style="margin:12px 0">A 股 ≥3 板候选池在 2023-2025 中段呈现结构性次日均值负偏，因此 ALL 资金曲线一路下行不代表因子失效，而是该期间不适合做"无脑跟进≥3 板"的策略。OOS 才是因子在游资活跃期的真实表现。</p>
      <div class="diag-grid">{"".join(diag_html)}</div>
    </details>
    '''


# ============================================================
# 图表容器
# ============================================================
def _chart_div(canvas_id, title, height=320, note=""):
    note_html = f'<p class="chart-note">{note}</p>' if note else ""
    return f'''
    <div class="chart-container">
      <h4>{title}</h4>
      {note_html}
      <div class="chart-wrap" style="position:relative;height:{height}px;width:100%;">
        <canvas id="{canvas_id}"></canvas>
      </div>
    </div>'''


# ============================================================
# 主图表区：OOS 优先
# ============================================================
def render_charts_section(factor_reports, strategy_scenarios) -> str:
    parts = ['<section><h2>🌟 OOS 样本外（推荐主视图）</h2>']
    parts.append('<p class="hint">区间：最近 6 个月 · 每日 score≥80 取 top-N · t+1 open 进 t+2 vwap 出 · 双成本 0.10%（万五佣金+印花税真实费率）</p>')

    oos_pure = find_strategy(strategy_scenarios, "OOS", "纯A3", "标准")
    oos_gate = find_strategy(strategy_scenarios, "OOS", "IC gate", "标准")

    if oos_pure or oos_gate:
        parts.append(_chart_div("oosEquityChart",
            "💰 OOS 资金曲线（纯 A3 vs 含 IC gate 对比）", 360,
            "蓝线 = 每日纯 A3；橙线 = 仅在 60 日 RankIC > 0.02 时下单"))
        parts.append(_chart_div("oosDrawdownChart", "📉 OOS 回撤曲线", 240))

    if oos_pure and oos_pure.get("metrics", {}).get("monthly_return"):
        parts.append(_chart_div("oosMonthlyChart", "📅 OOS 月度收益（纯 A3）", 240))

    parts.append('</section>')

    # ===== 因子诊断维度 =====
    parts.append('<section><h2>📊 因子诊断维度</h2>')
    parts.append('<p class="hint">这里展示因子本身的预测力，与策略层资金曲线无关</p>')

    all_r = find_factor(factor_reports, "ALL")
    if all_r and all_r.get("ic_series"):
        parts.append(_chart_div("icChart",
            "📈 IC / RankIC 时序（每日横截面）", 280,
            "蓝 = IC（Pearson）；橙 = RankIC（Spearman）。靠近 0 表示当日截面无法区分"))

    # 按板高 fwd_return 柱（从 parquet 计算）
    parts.append(_chart_div("streakFwdChart",
        "🪜 按连板高度的 forward_return 均值（来自 parquet）", 240,
        "若全部为负 = ≥3 板候选池整体次日均值是负的（A股结构性事实）"))

    # 按 signal 类型 fwd_return 柱
    parts.append(_chart_div("signalFwdChart",
        "🚦 按 signal 类型的 forward_return 均值", 240,
        "buy / watch / hold / unfillable 四档分别的次日实际表现"))

    parts.append('</section>')

    return "\n".join(parts)


# ============================================================
# 多组合矩阵
# ============================================================
def render_matrix_section(strategy_scenarios) -> str:
    if not strategy_scenarios:
        return ""

    intervals = [("OOS", "🌟 OOS 样本外"), ("IS", "IS 样本内"), ("ALL", "ALL 全样本")]
    groups = [("纯A3", "纯 A3"), ("IC gate", "含 IC gate")]
    costs = [("标准", "标准 0.10%"), ("压力", "压力 0.15%")]

    parts = ['<section><h2>🎯 多组合矩阵</h2>']
    parts.append('<p class="hint">3 区间 × 2 组 × 2 成本 共 12 个组合。绿色 = 正向，红色 = 负向，灰色 = 全样本含因子失效期</p>')

    parts.append('<table class="matrix-table">')
    parts.append('<tr><th>区间</th><th>组合</th><th>成本</th><th>信号数</th><th>胜率</th><th>夏普</th><th>ARR%</th><th>累计%</th><th>MDD%</th></tr>')

    for ikey, ilabel in intervals:
        for gkey, glabel in groups:
            for ckey, clabel in costs:
                s = find_strategy(strategy_scenarios, ikey, gkey, ckey)
                if s is None:
                    continue
                m = s.get("metrics", {})
                n = m.get("n_signals", 0)
                if n == 0:
                    continue
                arr = m.get("ARR_pct", 0)
                row_cls = "row-good" if (ikey == "OOS" and arr > 0) else ("row-muted" if ikey != "OOS" else "row-neutral")
                arr_cls = "cell-pos" if arr > 0 else "cell-neg"
                cum = m.get("累计收益_pct", 0)
                cum_cls = "cell-pos" if cum > 0 else "cell-neg"

                parts.append(f'<tr class="{row_cls}">'
                             f'<td><strong>{ilabel}</strong></td>'
                             f'<td>{glabel}</td>'
                             f'<td>{clabel}</td>'
                             f'<td>{n}</td>'
                             f'<td>{m.get("胜率", 0)*100:.1f}%</td>'
                             f'<td>{fmt(m.get("夏普"))}</td>'
                             f'<td class="{arr_cls}">{fmt(arr, "%")}</td>'
                             f'<td class="{cum_cls}">{fmt(cum, "%")}</td>'
                             f'<td>{fmt(m.get("MDD_pct"), "%")}</td>'
                             f'</tr>')
    parts.append('</table>')
    parts.append('</section>')
    return "\n".join(parts)


# ============================================================
# ALL 折叠诊断
# ============================================================
def render_all_diagnostic(strategy_scenarios) -> str:
    all_pure = find_strategy(strategy_scenarios, "ALL", "纯A3", "标准")
    if not all_pure:
        return ""
    parts = ['<section><details><summary><h2 style="display:inline-block;margin:0">📉 ALL 全样本诊断（点击展开）</h2></summary>']
    parts.append('<p class="hint" style="margin-top:16px">⚠️ 这个视图包含 2023-06 ~ 2025-12 的因子失效期，不代表当前因子能力。仅用于诊断"如果无脑跟进≥3 板会怎样"。</p>')
    parts.append(_chart_div("allEquityChart", "💸 ALL 全样本资金曲线（诊断）", 240))
    parts.append(_chart_div("allMonthlyChart", "📅 ALL 月度收益（诊断 — 看哪几个月赚 vs 亏）", 240))
    parts.append('</details></section>')
    return "\n".join(parts)


# ============================================================
# 最新信号 + 子因子雷达
# ============================================================
def render_latest_section(parquet_path) -> str:
    if not parquet_path.exists():
        return ""
    df = pd.read_parquet(parquet_path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    latest_date = df["trade_date"].max()
    latest = df[df["trade_date"] == latest_date].sort_values("rank").head(3)
    if len(latest) == 0:
        return ""

    parts = [f'<section><h2>📋 最新交易日信号 · {latest_date.date()}</h2>']
    parts.append('<div class="latest-grid">')

    radar_dimensions = ["concept_leader_score", "concept_followers_rate",
                        "early_seal_score", "seal_strength_proxy",
                        "blow_up_proxy", "position_60d"]
    radar_dimensions = [c for c in radar_dimensions if c in latest.columns]

    radar_data_list = []
    for _, r in latest.iterrows():
        sig = r["signal"]
        sig_cls = {"buy": "sig-buy", "watch": "sig-watch", "hold": "sig-hold",
                   "unfillable": "sig-unfillable"}.get(sig, "sig-muted")
        parts.append(f'''
        <div class="latest-card">
          <div class="latest-header">
            <span class="rank-badge">#{int(r["rank"])}</span>
            <span class="signal-badge {sig_cls}">{sig}</span>
          </div>
          <div class="latest-code">{r["ts_code"]}</div>
          <div class="latest-name">{r["name"]}</div>
          <div class="latest-score">{r["score"]:.1f}<small>/100</small></div>
          <div class="latest-stats">
            <div>连板: <strong>{int(r["limit_up_streak"])} 板</strong></div>
            <div>题材龙头: <strong>{r["concept_leader_score"]:.2f}</strong></div>
            <div>跟封率: <strong>{r["concept_followers_rate"]:.2f}</strong></div>
            <div>早封板: <strong>{r["early_seal_score"]:.2f}</strong></div>
            <div>炸板风险: <strong>{r["blow_up_proxy"]:.2f}</strong></div>
            <div>60日位置: <strong>{r["position_60d"]:.2f}</strong></div>
          </div>
        </div>''')

        # 雷达数据：归一化到 0-1（炸板风险已反转）
        vals = []
        for d in radar_dimensions:
            v = float(r.get(d, 0)) if pd.notna(r.get(d)) else 0
            if d == "blow_up_proxy":
                v = max(0, 1 - v * 5)  # 越低越好
            elif d == "seal_strength_proxy":
                # 已是 z-score 或类似，归一到 0-1
                v = max(0, min(1, (v + 2) / 4))
            else:
                v = max(0, min(1, v))
            vals.append(round(v, 3))
        radar_data_list.append({"label": str(r["ts_code"]), "data": vals})

    parts.append('</div>')

    if radar_dimensions and radar_data_list:
        parts.append(_chart_div("radarChart", "🎯 候选子因子雷达图", 400,
                                "外圈 = 子因子强 / 风险低；炸板风险已反转，越外越好"))

    parts.append('</section>')

    # 把雷达数据带出去给 JS
    return "\n".join(parts), radar_dimensions, radar_data_list


# ============================================================
# 数据透明度（信号 / 子项分布）
# ============================================================
def render_data_transparency(parquet_path) -> str:
    if not parquet_path.exists():
        return ""
    df = pd.read_parquet(parquet_path)

    parts = ['<section><details><summary><h2 style="display:inline-block;margin:0">🔬 数据透明度（点击展开）</h2></summary>']
    parts.append('<div class="two-col" style="margin-top:16px">')

    # 信号分布
    sig_dist = df["signal"].value_counts().to_dict()
    parts.append('<div><h4>signal 分布</h4><table><tr><th>类型</th><th>条数</th><th>占比</th></tr>')
    total = len(df)
    for k, v in sig_dist.items():
        parts.append(f'<tr><td>{k}</td><td>{v}</td><td>{v/total*100:.1f}%</td></tr>')
    parts.append('</table></div>')

    # 连板高度分布
    if "limit_up_streak" in df.columns:
        ld = df["limit_up_streak"].value_counts().sort_index()
        parts.append('<div><h4>连板高度分布</h4><table><tr><th>板数</th><th>条数</th></tr>')
        for k, v in ld.items():
            parts.append(f'<tr><td>{int(k)} 板</td><td>{v}</td></tr>')
        parts.append('</table></div>')
    parts.append('</div>')

    # 子项统计
    sub_cols = ["concept_leader_score", "concept_followers_rate", "early_seal_score",
                "seal_strength_proxy", "blow_up_proxy", "position_60d", "lhb_hotmoney"]
    sub_cols = [c for c in sub_cols if c in df.columns]
    if sub_cols:
        parts.append('<h4 style="margin-top:16px">子项统计（全样本）</h4><table>')
        parts.append('<tr><th>子项</th><th>均值</th><th>标准差</th><th>非空率</th></tr>')
        for c in sub_cols:
            parts.append(f'<tr><td>{c}</td><td>{df[c].mean():.4f}</td><td>{df[c].std():.4f}</td><td>{df[c].notna().mean()*100:.1f}%</td></tr>')
        parts.append('</table>')

    parts.append('</details></section>')
    return "\n".join(parts)


# ============================================================
# 因子检验表 + 策略表（保留）
# ============================================================
def render_factor_section(reports) -> str:
    if not reports:
        return ""
    parts = ['<section><details><summary><h2 style="display:inline-block;margin:0">📊 因子检验明细（点击展开）</h2></summary>']
    parts.append('<p class="hint" style="margin-top:16px">评估口径：<code>forward_return = vwap[t+2] / open[t+1] − 1</code></p>')
    for r in reports:
        if r.get("样本数", 0) == 0:
            continue
        parts.append(f'<h3>{r["label"]}</h3>')
        parts.append(f'<p class="meta">区间 {r["区间"]} · 样本数 {r["样本数"]} · 平均横截面宽度 {r["平均横截面宽度"]}</p>')
        parts.append('<div class="two-col">')
        parts.append('<div><h4>因子预测力</h4><table>')
        parts.append('<tr><th>指标</th><th>值</th></tr>')
        for k in ["IC", "RankIC", "ICIR", "RankICIR", "n_days_有效IC"]:
            parts.append(f'<tr><td>{k}</td><td>{fmt(r.get(k))}</td></tr>')
        parts.append('</table></div>')
        parts.append('<div><h4>策略层近似（毛收益）</h4><table>')
        parts.append('<tr><th>指标</th><th>值</th></tr>')
        for k, suf in [("IR(SHR*)", ""), ("CR", ""), ("ARR(%)", "%"), ("MDD(%)", "%"),
                       ("成交率", ""), ("换手率", "")]:
            parts.append(f'<tr><td>{k}</td><td>{fmt(r.get(k), suf)}</td></tr>')
        parts.append('</table></div>')
        parts.append('</div>')
    parts.append('</details></section>')
    return "\n".join(parts)


# ============================================================
# Chart.js 脚本
# ============================================================
def render_charts_script(factor_reports, strategy_scenarios, parquet_path,
                          radar_dimensions, radar_data_list) -> str:
    scripts = []
    base_opts = "responsive:true,maintainAspectRatio:false,animation:false"

    # ===== OOS 资金曲线（双线对比） =====
    oos_pure = find_strategy(strategy_scenarios, "OOS", "纯A3", "标准")
    oos_gate = find_strategy(strategy_scenarios, "OOS", "IC gate", "标准")
    if oos_pure or oos_gate:
        datasets = []
        labels_set = []
        if oos_pure:
            eq = oos_pure.get("metrics", {}).get("equity_curve", [])
            if eq:
                if not labels_set:
                    labels_set = [d for d, _ in eq]
                datasets.append({
                    "label": "纯 A3", "data": [v for _, v in eq],
                    "borderColor": "#3b82f6", "backgroundColor": "rgba(59,130,246,0.1)",
                    "borderWidth": 2, "pointRadius": 0, "tension": 0.1, "fill": True
                })
        if oos_gate:
            eq = oos_gate.get("metrics", {}).get("equity_curve", [])
            if eq:
                # 合并 labels（OOS pure 和 gate 可能起止不同）
                if not labels_set:
                    labels_set = [d for d, _ in eq]
                # 对齐 gate 数据到 pure 的 labels
                gate_map = dict(eq)
                gate_data = [gate_map.get(d) for d in labels_set]
                datasets.append({
                    "label": "含 IC gate", "data": gate_data,
                    "borderColor": "#f59e0b", "backgroundColor": "rgba(245,158,11,0.05)",
                    "borderWidth": 2, "pointRadius": 0, "tension": 0.1, "fill": False,
                    "spanGaps": True
                })
        if datasets:
            scripts.append(f'''
              new Chart(document.getElementById('oosEquityChart').getContext('2d'), {{
                type:'line',
                data:{{labels:{json.dumps(labels_set)}, datasets:{json.dumps(datasets)}}},
                options:{{{base_opts},
                  scales:{{x:{{ticks:{{maxTicksLimit:10,autoSkip:true}}}},
                    y:{{title:{{display:true,text:'资金倍数（初始 1.0）'}}}}}},
                  plugins:{{legend:{{display:true,position:'top'}},tooltip:{{mode:'index',intersect:false}}}}}}
              }});''')

    # ===== OOS 回撤 =====
    if oos_pure:
        dd = oos_pure.get("metrics", {}).get("drawdown_curve", [])
        if dd:
            labels = [d for d, _ in dd]
            data = [v for _, v in dd]
            scripts.append(f'''
              new Chart(document.getElementById('oosDrawdownChart').getContext('2d'), {{
                type:'line',
                data:{{labels:{json.dumps(labels)},
                  datasets:[{{label:'回撤 %', data:{json.dumps(data)},
                    borderColor:'#ef4444', backgroundColor:'rgba(239,68,68,0.2)',
                    borderWidth:1.5, pointRadius:0, fill:true}}]}},
                options:{{{base_opts},
                  scales:{{x:{{ticks:{{maxTicksLimit:10,autoSkip:true}}}},
                    y:{{title:{{display:true,text:'回撤 %'}}}}}},
                  plugins:{{legend:{{display:false}}}}}}
              }});''')

    # ===== OOS 月度 =====
    if oos_pure and oos_pure.get("metrics", {}).get("monthly_return"):
        ms = oos_pure["metrics"]["monthly_return"]
        labels = [d for d, _ in ms]
        data = [v for _, v in ms]
        colors = ['rgba(16,185,129,0.8)' if v >= 0 else 'rgba(239,68,68,0.8)' for v in data]
        scripts.append(f'''
          new Chart(document.getElementById('oosMonthlyChart').getContext('2d'), {{
            type:'bar',
            data:{{labels:{json.dumps(labels)},
              datasets:[{{label:'月度收益 %', data:{json.dumps(data)}, backgroundColor:{json.dumps(colors)}}}]}},
            options:{{{base_opts},
              scales:{{y:{{title:{{display:true,text:'月度收益 %'}}}}}},
              plugins:{{legend:{{display:false}}}}}}
          }});''')

    # ===== IC 时序 =====
    all_r = find_factor(factor_reports, "ALL")
    if all_r and all_r.get("ic_series"):
        ic = all_r["ic_series"]
        ric = all_r.get("rank_ic_series", [])
        labels = [d for d, _ in ic]
        data_ic = [v for _, v in ic]
        data_ric = [v for _, v in ric] if ric else []
        ric_ds = f''', {{label:'RankIC', data:{json.dumps(data_ric)}, borderColor:'#f59e0b', borderWidth:1, pointRadius:0, tension:0.1}}''' if data_ric else ""
        scripts.append(f'''
          new Chart(document.getElementById('icChart').getContext('2d'), {{
            type:'line',
            data:{{labels:{json.dumps(labels)},
              datasets:[
                {{label:'IC', data:{json.dumps(data_ic)}, borderColor:'#3b82f6', borderWidth:1, pointRadius:0, tension:0.1}}
                {ric_ds}
              ]}},
            options:{{{base_opts},
              scales:{{x:{{ticks:{{maxTicksLimit:12,autoSkip:true}}}},
                y:{{title:{{display:true,text:'IC 值'}}}}}}}}
          }});''')

    # ===== 按 streak 板高 fwd_return =====
    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)
        if "limit_up_streak" in df.columns and "forward_return" in df.columns:
            grp = df.dropna(subset=["forward_return"]).groupby("limit_up_streak")["forward_return"]
            streak_stats = grp.agg(["mean", "count"]).reset_index()
            streak_stats = streak_stats[streak_stats["count"] >= 5]
            labels = [f"{int(s)} 板" for s in streak_stats["limit_up_streak"]]
            data = [round(v * 100, 4) for v in streak_stats["mean"]]
            counts = [int(c) for c in streak_stats["count"]]
            colors = ['rgba(16,185,129,0.8)' if v > 0 else 'rgba(239,68,68,0.8)' for v in data]
            scripts.append(f'''
              new Chart(document.getElementById('streakFwdChart').getContext('2d'), {{
                type:'bar',
                data:{{labels:{json.dumps(labels)},
                  datasets:[{{label:'平均 forward_return %', data:{json.dumps(data)}, backgroundColor:{json.dumps(colors)}}}]}},
                options:{{{base_opts},
                  scales:{{y:{{title:{{display:true,text:'forward_return %'}}}}}},
                  plugins:{{legend:{{display:false}},
                    tooltip:{{callbacks:{{afterLabel:function(ctx){{return 'n='+{json.dumps(counts)}[ctx.dataIndex]+' 样本';}}}}}}}}}}
              }});''')

        # ===== 按 signal 类型 fwd_return =====
        if "signal" in df.columns and "forward_return" in df.columns:
            grp = df.dropna(subset=["forward_return"]).groupby("signal")["forward_return"]
            sig_stats = grp.agg(["mean", "count"]).reset_index()
            order = ["buy", "watch", "hold", "unfillable"]
            sig_stats["order"] = sig_stats["signal"].apply(lambda s: order.index(s) if s in order else 99)
            sig_stats = sig_stats.sort_values("order")
            labels = list(sig_stats["signal"])
            data = [round(v * 100, 4) for v in sig_stats["mean"]]
            counts = [int(c) for c in sig_stats["count"]]
            colors = ['rgba(16,185,129,0.8)' if v > 0 else 'rgba(239,68,68,0.8)' for v in data]
            scripts.append(f'''
              new Chart(document.getElementById('signalFwdChart').getContext('2d'), {{
                type:'bar',
                data:{{labels:{json.dumps(labels)},
                  datasets:[{{label:'平均 forward_return %', data:{json.dumps(data)}, backgroundColor:{json.dumps(colors)}}}]}},
                options:{{{base_opts},
                  scales:{{y:{{title:{{display:true,text:'forward_return %'}}}}}},
                  plugins:{{legend:{{display:false}},
                    tooltip:{{callbacks:{{afterLabel:function(ctx){{return 'n='+{json.dumps(counts)}[ctx.dataIndex]+' 样本';}}}}}}}}}}
              }});''')

    # ===== ALL 折叠诊断 =====
    all_pure = find_strategy(strategy_scenarios, "ALL", "纯A3", "标准")
    if all_pure:
        eq = all_pure.get("metrics", {}).get("equity_curve", [])
        if eq:
            labels = [d for d, _ in eq]
            data = [v for _, v in eq]
            scripts.append(f'''
              new Chart(document.getElementById('allEquityChart').getContext('2d'), {{
                type:'line',
                data:{{labels:{json.dumps(labels)},
                  datasets:[{{label:'资金倍数', data:{json.dumps(data)},
                    borderColor:'#94a3b8', backgroundColor:'rgba(148,163,184,0.1)',
                    borderWidth:1.5, pointRadius:0, fill:true}}]}},
                options:{{{base_opts},
                  scales:{{x:{{ticks:{{maxTicksLimit:10,autoSkip:true}}}},
                    y:{{title:{{display:true,text:'资金倍数'}}}}}},
                  plugins:{{legend:{{display:false}}}}}}
              }});''')
        ms = all_pure["metrics"].get("monthly_return", [])
        if ms:
            labels = [d for d, _ in ms]
            data = [v for _, v in ms]
            colors = ['rgba(16,185,129,0.7)' if v >= 0 else 'rgba(239,68,68,0.7)' for v in data]
            scripts.append(f'''
              new Chart(document.getElementById('allMonthlyChart').getContext('2d'), {{
                type:'bar',
                data:{{labels:{json.dumps(labels)},
                  datasets:[{{label:'月度 %', data:{json.dumps(data)}, backgroundColor:{json.dumps(colors)}}}]}},
                options:{{{base_opts},
                  scales:{{y:{{title:{{display:true,text:'月度 %'}}}}}},
                  plugins:{{legend:{{display:false}}}}}}
              }});''')

    # ===== 雷达图 =====
    if radar_dimensions and radar_data_list:
        dim_labels = [d.replace("_score", "").replace("_proxy", "_inv").replace("_", " ") for d in radar_dimensions]
        palette = ["#3b82f6", "#10b981", "#f59e0b"]
        datasets = []
        for i, item in enumerate(radar_data_list[:3]):
            c = palette[i % len(palette)]
            rgba = f"rgba({int(c[1:3],16)},{int(c[3:5],16)},{int(c[5:7],16)},0.2)"
            datasets.append({
                "label": item["label"], "data": item["data"],
                "borderColor": c, "backgroundColor": rgba, "borderWidth": 2,
                "pointRadius": 3
            })
        scripts.append(f'''
          new Chart(document.getElementById('radarChart').getContext('2d'), {{
            type:'radar',
            data:{{labels:{json.dumps(dim_labels)}, datasets:{json.dumps(datasets)}}},
            options:{{{base_opts},
              scales:{{r:{{min:0, max:1, ticks:{{stepSize:0.2}}}}}},
              plugins:{{legend:{{display:true,position:'top'}}}}}}
          }});''')

    return "\n".join(scripts)


# ============================================================
# 主 render
# ============================================================
def render_html(factor_reports, strategy_scenarios, parquet_path) -> str:
    parquet_stats = {}
    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)
        parquet_stats = {
            "rows": len(df),
            "trade_dates": df["trade_date"].nunique(),
            "ts_codes": df["ts_code"].nunique(),
        }

    review = render_factor_review(factor_reports or [], strategy_scenarios or [], parquet_path)
    recent_days = render_recent_days(parquet_path, n_days=20)
    hero = render_hero(factor_reports or [], strategy_scenarios or [])
    charts = render_charts_section(factor_reports, strategy_scenarios)
    matrix = render_matrix_section(strategy_scenarios)
    all_diag = render_all_diagnostic(strategy_scenarios)
    latest_result = render_latest_section(parquet_path)
    if isinstance(latest_result, tuple):
        latest_sec, radar_dims, radar_data = latest_result
    else:
        latest_sec, radar_dims, radar_data = "", [], []
    transparency = render_data_transparency(parquet_path)
    factor_sec = render_factor_section(factor_reports)
    charts_script = render_charts_script(
        factor_reports, strategy_scenarios, parquet_path, radar_dims, radar_data)

    css = """
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
           margin: 0; padding: 32px 24px; background: #f8fafc; color: #1e293b; line-height: 1.6; }
    .container { max-width: 1320px; margin: 0 auto; }
    h1 { font-size: 28px; margin: 0 0 8px; color: #0f172a; }
    h2 { font-size: 22px; margin: 0 0 16px; padding-bottom: 8px; border-bottom: 2px solid #e2e8f0; color: #0f172a; }
    h3 { font-size: 18px; margin: 20px 0 12px; color: #334155; }
    h4 { font-size: 15px; margin: 16px 0 8px; color: #475569; }
    .header { background: linear-gradient(135deg, #1e293b 0%, #334155 100%); color: white;
              padding: 24px; border-radius: 12px; margin-bottom: 32px; }
    .header h1 { color: white; }
    .header .sub { opacity: 0.85; font-size: 14px; }

    /* Hero */
    .hero-banner { background: linear-gradient(135deg, #ecfdf5 0%, #f0f9ff 100%);
                   border: 2px solid #10b981; border-radius: 12px; padding: 20px;
                   margin: 24px 0; }
    .hero-label { font-size: 14px; font-weight: 600; color: #047857;
                  letter-spacing: 0.5px; margin-bottom: 12px; }
    .metric-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
                   gap: 12px; }
    .metric-card { background: white; padding: 14px; border-radius: 8px;
                   box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    .metric-title { font-size: 11px; color: #64748b; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.3px; }
    .metric-value { font-size: 24px; font-weight: 700; line-height: 1.2; }
    .metric-sub { font-size: 11px; color: #94a3b8; margin-top: 2px; }
    .metric-primary .metric-value { color: #3b82f6; }
    .metric-good .metric-value { color: #10b981; }
    .metric-warn .metric-value { color: #f59e0b; }
    .metric-muted .metric-value { color: #64748b; }

    /* 诊断条 */
    .diag-bar { background: #f1f5f9; border-radius: 8px; padding: 16px 20px;
                margin: 16px 0 24px; cursor: pointer; }
    .diag-bar summary { font-weight: 600; color: #475569; cursor: pointer; outline: none; }
    .diag-bar summary::marker { color: #94a3b8; }
    .diag-grid { display: flex; flex-wrap: wrap; gap: 10px; }
    .diag-pill { display: inline-flex; gap: 6px; align-items: center;
                 background: white; padding: 6px 12px; border-radius: 20px;
                 font-size: 12px; color: #64748b; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
    .diag-pill strong { color: #1e293b; font-weight: 600; }

    section { background: white; padding: 24px; border-radius: 12px; margin: 24px 0;
              box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
    .hint { color: #64748b; font-size: 13px; margin: 0 0 16px; }
    .meta { color: #64748b; font-size: 13px; }
    code { background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 13px; }

    /* Table */
    table { width: 100%; border-collapse: collapse; margin: 8px 0 16px; font-size: 14px; }
    th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }
    th { background: #f8fafc; font-weight: 600; color: #475569; }
    tr:hover { background: #f8fafc; }
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
    @media (max-width: 768px) { .two-col { grid-template-columns: 1fr; } }

    /* 多组合矩阵 */
    .matrix-table tr.row-good td { background: linear-gradient(90deg, #ecfdf5 0%, transparent 50%); }
    .matrix-table tr.row-muted td { background: #fafafa; color: #94a3b8; }
    .matrix-table .cell-pos { color: #059669; font-weight: 600; }
    .matrix-table .cell-neg { color: #dc2626; font-weight: 500; }

    /* Chart */
    .chart-container { margin: 24px 0; padding: 16px; background: #fafafa; border-radius: 8px; }
    .chart-container h4 { margin-top: 0; }
    .chart-note { font-size: 12px; color: #94a3b8; margin: -4px 0 12px; font-style: italic; }

    /* Latest signal cards */
    .latest-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
                   gap: 16px; margin-bottom: 24px; }
    .latest-card { background: linear-gradient(180deg, white 0%, #f8fafc 100%);
                   border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px; }
    .latest-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
    .rank-badge { background: #1e293b; color: white; font-size: 12px; font-weight: 700;
                  padding: 2px 8px; border-radius: 999px; }
    .signal-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600;
                    text-transform: uppercase; letter-spacing: 0.5px; }
    .sig-buy { background: #10b981; color: white; }
    .sig-watch { background: #f59e0b; color: white; }
    .sig-hold { background: #64748b; color: white; }
    .sig-unfillable { background: #ef4444; color: white; }
    .latest-code { font-size: 13px; color: #64748b; margin-top: 4px; font-family: monospace; }
    .latest-name { font-size: 18px; font-weight: 600; color: #0f172a; margin: 4px 0; }
    .latest-score { font-size: 36px; font-weight: 800; color: #3b82f6; margin: 8px 0; }
    .latest-score small { font-size: 14px; color: #94a3b8; font-weight: 400; }
    .latest-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 4px;
                    font-size: 12px; color: #64748b; }
    .latest-stats strong { color: #1e293b; }

    details > summary { cursor: pointer; outline: none; }
    details > summary::marker { color: #94a3b8; }

    /* ===== 因子研判 ===== */
    .review-section { background: linear-gradient(135deg, #fafbff 0%, #f8fafc 100%);
                      border-left: 4px solid #3b82f6; }
    .verdict { display: flex; align-items: center; gap: 16px; padding: 16px 20px;
               border-radius: 8px; margin: 16px 0; }
    .verdict-good { background: #ecfdf5; border-left: 4px solid #10b981; }
    .verdict-warn { background: #fffbeb; border-left: 4px solid #f59e0b; }
    .verdict-bad { background: #fef2f2; border-left: 4px solid #ef4444; }
    .verdict-emoji { font-size: 32px; }
    .verdict-text { font-size: 16px; font-weight: 600; color: #1e293b; flex: 1; }
    .overall { background: white; padding: 16px; border-radius: 8px; margin: 12px 0 20px;
               border: 1px solid #e2e8f0; color: #334155; }
    .overall strong { color: #1e293b; background: #fef3c7; padding: 1px 6px; border-radius: 3px; }

    .findings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
                     gap: 12px; margin: 8px 0 20px; }
    .finding { padding: 12px 16px; border-radius: 8px; border-left: 3px solid; background: white; }
    .finding-good { border-color: #10b981; background: #f0fdf4; }
    .finding-warn { border-color: #f59e0b; background: #fffbeb; }
    .finding-bad { border-color: #ef4444; background: #fef2f2; }
    .finding-neutral { border-color: #94a3b8; background: #f8fafc; }
    .finding-title { font-size: 13px; font-weight: 700; color: #1e293b; margin-bottom: 4px; }
    .finding-text { font-size: 13px; color: #475569; line-height: 1.5; }
    .finding-text strong { color: #1e293b; }

    .advice-list { list-style: none; padding: 0; margin: 8px 0; }
    .advice-list li { padding: 8px 12px; background: white; margin: 6px 0; border-radius: 6px;
                      font-size: 13px; color: #334155; border: 1px solid #e2e8f0; }
    .advice-icon { display: inline-block; margin-right: 8px; }

    /* ===== 最近 N 日明细 ===== */
    .recent-wrap { max-height: 600px; overflow-y: auto; border: 1px solid #e2e8f0; border-radius: 8px; }
    .recent-table { font-size: 12px; margin: 0; }
    .recent-table th { position: sticky; top: 0; z-index: 1; background: #f1f5f9; }
    .recent-table tr:hover { background: #f8fafc; }
    .recent-table .ts-code { font-family: monospace; color: #64748b; }
    .rank-mini { background: #1e293b; color: white; font-size: 11px; font-weight: 600;
                 padding: 1px 6px; border-radius: 4px; }
    .signal-mini { font-size: 10px; padding: 1px 6px; border-radius: 3px;
                   font-weight: 600; text-transform: uppercase; }
    .cell-pos { color: #059669; font-weight: 600; }
    .cell-neg { color: #dc2626; font-weight: 600; }

    .daily-pnl-table { font-size: 13px; }
    .daily-pnl-table th, .daily-pnl-table td { text-align: right; }
    .daily-pnl-table th:first-child, .daily-pnl-table td:first-child { text-align: left; }
    """

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Alpha-A3 连板龙头接力 · 回测报告</title>
<style>{css}</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Alpha-A3 · 连板龙头接力</h1>
    <div class="sub">回测可视化报告 · 生成于 {datetime.now().isoformat(timespec='seconds')}</div>
    <div class="sub">parquet 样本: {parquet_stats.get('rows', '—')} 行 · {parquet_stats.get('trade_dates', '—')} 交易日 · {parquet_stats.get('ts_codes', '—')} 标的</div>
  </div>

  {review}

  {hero}

  {latest_sec}

  {recent_days}

  {charts}

  {matrix}

  {all_diag}

  {factor_sec}

  {transparency}

  <section style="text-align:center; color:#94a3b8; font-size:12px;">
    <p>Alpha-A3 连板龙头接力因子 · 量枢院 · {datetime.now().year}</p>
  </section>
</div>
<script>
{charts_script}
</script>
</body>
</html>"""
    return html


def main():
    ap = argparse.ArgumentParser(description="A3 回测 HTML 可视化（v3 多维度版）")
    ap.add_argument("--factor-parquet", default=str(DEFAULT_FACTOR_PARQUET))
    ap.add_argument("--factor-report", default=str(DEFAULT_FACTOR_JSON))
    ap.add_argument("--strategy-report", default=str(DEFAULT_STRATEGY_JSON))
    ap.add_argument("--out", default=str(DEFAULT_HTML))
    args = ap.parse_args()

    factor_reports = safe_load_json(Path(args.factor_report))
    strategy_scenarios = safe_load_json(Path(args.strategy_report))
    parquet_path = Path(args.factor_parquet)

    html = render_html(factor_reports, strategy_scenarios, parquet_path)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"HTML 报告: {args.out}")
    print(f"  大小: {Path(args.out).stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
