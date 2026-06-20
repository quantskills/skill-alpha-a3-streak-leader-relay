#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpha-A3 HTML 可视化报告（升级版）
================================================================
- 顶部 hero 卡片（关键指标大字）
- 资金曲线图（Chart.js · 组A vs 组B 对比）
- 回撤曲线
- IC / RankIC 时序图
- 月度收益热力图
- 分层 Q1-Q5 柱状图
- market_state 分布饼图
- 信号样例表

不依赖任何 Python 绘图库，纯 Chart.js CDN，可邮件发送。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ALPHA_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTOR_PARQUET = ALPHA_ROOT / "alpha-a3-streak-leader-relay-production" / "database.parquet"
DEFAULT_FACTOR_JSON = ALPHA_ROOT / "alpha-a3-streak-leader-relay-production" / "backtest_metrics.json"
DEFAULT_STRATEGY_JSON = ALPHA_ROOT / "alpha-a3-streak-leader-relay-production" / "strategy_backtest_metrics.json"
DEFAULT_HTML = ALPHA_ROOT / "alpha-a3-streak-leader-relay-production" / "backtest_report.html"


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


# ============================================================
# 顶部 hero
# ============================================================
def render_hero(factor_reports, strategy_scenarios) -> str:
    all_r = next((r for r in factor_reports if "ALL" in r["label"]), None) if factor_reports else None
    oos_r = next((r for r in factor_reports if "OOS" in r["label"]), None) if factor_reports else None
    strat_all = next((s for s in strategy_scenarios
                      if "ALL" in s["label"] and "标准" in s["label"]),
                     None) if strategy_scenarios else None
    strat_oos = next((s for s in strategy_scenarios
                      if "OOS" in s["label"] and "标准" in s["label"]),
                     None) if strategy_scenarios else None

    cards = []
    if all_r:
        cards += [
            ("IC (ALL)", fmt(all_r.get("IC")), "#3b82f6"),
            ("ICIR (ALL)", fmt(all_r.get("ICIR")), "#3b82f6"),
        ]
    if oos_r:
        cards += [
            ("IC (OOS)", fmt(oos_r.get("IC")), "#6366f1"),
            ("Q5-Q1 多空 (OOS)", fmt(oos_r.get("分层收益(Q1-Q5)", {}).get("Q5-Q1 (多空)", 0) * 100, "%"), "#6366f1"),
        ]
    if strat_all:
        m = strat_all.get("metrics", {})
        cards += [
            ("夏普 (ALL)", fmt(m.get("夏普")), "#64748b"),
            ("ARR% (ALL)", fmt(m.get("ARR_pct"), "%"), "#64748b"),
            ("MDD% (ALL)", fmt(m.get("MDD_pct"), "%"), "#ef4444"),
        ]
    if strat_oos:
        m = strat_oos.get("metrics", {})
        cards += [
            ("夏普 (OOS)", fmt(m.get("夏普")), "#10b981"),
            ("ARR% (OOS)", fmt(m.get("ARR_pct"), "%"), "#10b981"),
            ("胜率 (OOS)", f"{m.get('胜率', 0)*100:.1f}%", "#10b981"),
        ]

    parts = []
    for title, value, color in cards:
        parts.append(f'''
        <div class="metric-card">
          <div class="metric-title">{title}</div>
          <div class="metric-value" style="color:{color}">{value}</div>
        </div>''')
    return f'<div class="metric-grid">{"".join(parts)}</div>'


# ============================================================
# 图表 ID 全局唯一
# ============================================================
def _chart_div(canvas_id, title, height=320):
    """图表容器：固定高度 + 避免 Chart.js 撑得过高。"""
    return f'''
    <div class="chart-container">
      <h4>{title}</h4>
      <div class="chart-wrap" style="position:relative;height:{height}px;width:100%;">
        <canvas id="{canvas_id}"></canvas>
      </div>
    </div>'''


def render_charts_section(factor_reports, strategy_scenarios) -> str:
    """资金曲线 + IC 时序 + 月度热力 + 分层柱"""
    if not factor_reports and not strategy_scenarios:
        return ""

    all_r = next((r for r in factor_reports if "ALL" in r["label"]), None) if factor_reports else None
    strat_all = next((s for s in strategy_scenarios
                      if "ALL" in s["label"] and "标准" in s["label"]),
                     None) if strategy_scenarios else None

    parts = ['<section><h2>📈 图表概览</h2>']

    # 1) 资金曲线
    if strat_all:
        eq = strat_all.get("metrics", {}).get("equity_curve", [])
        if eq:
            parts.append(_chart_div("equityChart", "💰 资金曲线（标准成本 0.30%，全样本）", 320))

    # 2) 回撤曲线
    if strat_all:
        dd = strat_all.get("metrics", {}).get("drawdown_curve", [])
        if dd:
            parts.append(_chart_div("drawdownChart", "📉 回撤曲线", 240))

    # 3) IC / RankIC 时序
    if all_r and all_r.get("ic_series"):
        parts.append(_chart_div("icChart", "📊 IC / RankIC 时序（每日横截面 IC）", 280))

    # 4) 月度收益柱
    if strat_all and strat_all.get("metrics", {}).get("monthly_return"):
        parts.append(_chart_div("monthlyChart", "📅 月度收益柱图", 240))

    # 5) Q1-Q5 分层柱图
    if all_r and all_r.get("分层收益(Q1-Q5)"):
        parts.append(_chart_div("quintileChart", "🪜 分层收益（按 factor_value 等频 5 层）", 240))

    parts.append('</section>')
    return "\n".join(parts)


def render_charts_script(factor_reports, strategy_scenarios) -> str:
    """Chart.js 初始化脚本"""
    all_r = next((r for r in factor_reports if "ALL" in r["label"]), None) if factor_reports else None
    strat_all = next((s for s in strategy_scenarios
                      if "ALL" in s["label"] and "标准" in s["label"]),
                     None) if strategy_scenarios else None

    # 通用 options（关键：maintainAspectRatio:false + 父容器固定高度限定）
    common_opts = '''
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: {{intersect: false, mode: 'index'}},'''

    scripts = []

    # 1) 资金曲线
    if strat_all:
        eq = strat_all.get("metrics", {}).get("equity_curve", [])
        if eq:
            labels = [d for d, _ in eq]
            data = [v for _, v in eq]
            scripts.append(f'''
              new Chart(document.getElementById('equityChart').getContext('2d'), {{
                type:'line',
                data:{{labels:{json.dumps(labels)},
                  datasets:[{{label:'资金倍数', data:{json.dumps(data)}, borderColor:'#3b82f6', backgroundColor:'rgba(59,130,246,0.1)', borderWidth:2, pointRadius:0, tension:0.1, fill:true}}]}},
                options:{{responsive:true,maintainAspectRatio:false,animation:false,
                  scales:{{x:{{ticks:{{maxTicksLimit:12,autoSkip:true}}}}, y:{{title:{{display:true,text:'累计资金倍数'}}}}}},
                  plugins:{{legend:{{display:false}}, tooltip:{{mode:'index',intersect:false}}}}}}
              }});''')

    # 2) 回撤曲线
    if strat_all:
        dd = strat_all.get("metrics", {}).get("drawdown_curve", [])
        if dd:
            labels = [d for d, _ in dd]
            data = [v for _, v in dd]
            scripts.append(f'''
              new Chart(document.getElementById('drawdownChart').getContext('2d'), {{
                type:'line',
                data:{{labels:{json.dumps(labels)},
                  datasets:[{{label:'回撤 %', data:{json.dumps(data)}, borderColor:'#ef4444', backgroundColor:'rgba(239,68,68,0.2)', borderWidth:1.5, pointRadius:0, fill:true}}]}},
                options:{{responsive:true,maintainAspectRatio:false,animation:false,
                  scales:{{x:{{ticks:{{maxTicksLimit:12,autoSkip:true}}}}, y:{{title:{{display:true,text:'回撤 %'}}}}}},
                  plugins:{{legend:{{display:false}}}}}}
              }});''')

    # 3) IC / RankIC 时序
    if all_r and all_r.get("ic_series"):
        ic = all_r.get("ic_series", [])
        ric = all_r.get("rank_ic_series", [])
        if ic:
            labels = [d for d, _ in ic]
            data_ic = [v for _, v in ic]
            data_ric = [v for _, v in ric] if ric else []
            scripts.append(f'''
              new Chart(document.getElementById('icChart').getContext('2d'), {{
                type:'line',
                data:{{labels:{json.dumps(labels)},
                  datasets:[
                    {{label:'IC', data:{json.dumps(data_ic)}, borderColor:'#3b82f6', borderWidth:1, pointRadius:0, tension:0.1}},
                    {{label:'RankIC', data:{json.dumps(data_ric)}, borderColor:'#f59e0b', borderWidth:1, pointRadius:0, tension:0.1}}
                  ]}},
                options:{{responsive:true,maintainAspectRatio:false,animation:false,
                  scales:{{x:{{ticks:{{maxTicksLimit:12,autoSkip:true}}}}, y:{{title:{{display:true,text:'IC 值'}}}}}}}}
              }});''')

    # 4) 月度收益柱
    if strat_all and strat_all.get("metrics", {}).get("monthly_return"):
        ms = strat_all["metrics"]["monthly_return"]
        labels = [d for d, _ in ms]
        data = [v for _, v in ms]
        colors = ['rgba(16,185,129,0.7)' if v >= 0 else 'rgba(239,68,68,0.7)' for v in data]
        scripts.append(f'''
          new Chart(document.getElementById('monthlyChart').getContext('2d'), {{
            type:'bar',
            data:{{labels:{json.dumps(labels)}, datasets:[{{label:'月度收益 %', data:{json.dumps(data)}, backgroundColor:{json.dumps(colors)}}}]}},
            options:{{responsive:true,maintainAspectRatio:false,animation:false,
              scales:{{y:{{title:{{display:true,text:'收益 %'}}}}}},
              plugins:{{legend:{{display:false}}}}}}
          }});''')

    # 5) Q1-Q5 柱
    if all_r and all_r.get("分层收益(Q1-Q5)"):
        q = all_r["分层收益(Q1-Q5)"]
        labels = [k for k in ["Q1", "Q2", "Q3", "Q4", "Q5"] if k in q]
        data = [round(q.get(k, 0) * 100, 4) for k in labels]
        colors = ['rgba(239,68,68,0.7)' if v < 0 else 'rgba(16,185,129,0.7)' for v in data]
        scripts.append(f'''
          new Chart(document.getElementById('quintileChart').getContext('2d'), {{
            type:'bar',
            data:{{labels:{json.dumps(labels)}, datasets:[{{label:'平均 forward_return %', data:{json.dumps(data)}, backgroundColor:{json.dumps(colors)}}}]}},
            options:{{responsive:true,maintainAspectRatio:false,animation:false,
              scales:{{y:{{title:{{display:true,text:'平均 forward_return %'}}}}}},
              plugins:{{legend:{{display:false}}}}}}
          }});''')

    return "\n".join(scripts)


# ============================================================
# 信号 / 因子 / 策略 表格区
# ============================================================
def render_factor_section(reports) -> str:
    if not reports:
        return ""
    parts = ['<section><h2>📊 因子检验</h2>']
    parts.append('<p class="hint">评估口径：<code>forward_return = vwap[t+2] / open[t+1] − 1</code> · 不可成交跳过：t+1 一字板 / 开盘涨停 / 停牌</p>')
    for r in reports:
        if r.get("样本数", 0) == 0:
            continue
        parts.append(f'<h3>{r["label"]}</h3>')
        parts.append(f'<p class="meta">区间 {r["区间"]} · 样本数 {r["样本数"]} · 平均横截面宽度 {r["平均横截面宽度"]}</p>')
        parts.append('<div class="two-col">')
        # 因子预测力
        parts.append('<div><h4>因子预测力</h4><table>')
        parts.append('<tr><th>指标</th><th>值</th></tr>')
        for k in ["IC", "RankIC", "ICIR", "RankICIR", "n_days_有效IC"]:
            parts.append(f'<tr><td>{k}</td><td>{fmt(r.get(k))}</td></tr>')
        parts.append('</table></div>')
        # 策略层近似
        parts.append('<div><h4>策略层近似（毛收益）</h4><table>')
        parts.append('<tr><th>指标</th><th>值</th></tr>')
        for k, suf in [("IR(SHR*)", ""), ("CR", ""), ("ARR(%)", "%"), ("MDD(%)", "%"),
                       ("成交率", ""), ("换手率", "")]:
            parts.append(f'<tr><td>{k}</td><td>{fmt(r.get(k), suf)}</td></tr>')
        parts.append('</table></div>')
        parts.append('</div>')

        # 分层
        q = r.get("分层收益(Q1-Q5)", {})
        spread = q.get("Q5-Q1 (多空)")
        if spread is not None:
            parts.append(f'<p class="spread">⭐ <strong>Q5-Q1 多空: {spread*100:+.4f}%</strong>（{r["label"]}）</p>')
    parts.append('</section>')
    return "\n".join(parts)


def render_strategy_section(scenarios) -> str:
    if not scenarios:
        return ""
    parts = ['<section><h2>💰 策略回测</h2>']
    parts.append('<p class="hint">每日 score≥80 取 top-N 等权分仓 · t+1 open 进 t+2 vwap 出 · 双成本扣除</p>')

    parts.append('<table>')
    parts.append('<tr><th>区间</th><th>成本</th><th>n</th><th>胜率</th><th>赔率</th><th>夏普</th><th>ARR%</th><th>MDD%</th><th>累计%</th><th>换手</th></tr>')
    for s in scenarios:
        m = s.get("metrics", {})
        if m.get("n_signals", 0) == 0:
            continue
        row_lbl = s["label"].split(" · ")
        interval = row_lbl[0] if len(row_lbl) > 0 else "?"
        cost = row_lbl[1] if len(row_lbl) > 1 else "?"
        parts.append(f'<tr>'
                     f'<td>{interval}</td><td>{cost}</td>'
                     f'<td>{m.get("n_signals", 0)}</td>'
                     f'<td>{m.get("胜率", 0)*100:.2f}%</td>'
                     f'<td>{fmt(m.get("赔率"))}</td>'
                     f'<td>{fmt(m.get("夏普"))}</td>'
                     f'<td>{fmt(m.get("ARR_pct"), "%")}</td>'
                     f'<td>{fmt(m.get("MDD_pct"), "%")}</td>'
                     f'<td>{fmt(m.get("累计收益_pct"), "%")}</td>'
                     f'<td>{fmt(m.get("换手率"))}</td>'
                     f'</tr>')
    parts.append('</table></section>')
    return "\n".join(parts)


def render_signal_section(parquet) -> str:
    if not parquet.exists():
        return ""
    df = pd.read_parquet(parquet)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    parts = ['<section><h2>📋 信号 / 分布概览</h2><div class="two-col">']

    # 信号分布
    sig_dist = df["signal"].value_counts().to_dict()
    parts.append('<div><h4>signal 分布</h4><table><tr><th>类型</th><th>条数</th><th>占比</th></tr>')
    total = len(df)
    for k, v in sig_dist.items():
        parts.append(f'<tr><td>{k}</td><td>{v}</td><td>{v/total*100:.1f}%</td></tr>')
    parts.append('</table></div>')

    # 连板高度分布（与 signal 分布并列）
    if "limit_up_streak" in df.columns:
        ld = df["limit_up_streak"].value_counts().sort_index()
        parts.append('<div><h4>连板高度分布</h4><table><tr><th>板数</th><th>条数</th></tr>')
        for k, v in ld.items():
            parts.append(f'<tr><td>{k} 板</td><td>{v}</td></tr>')
        parts.append('</table></div>')
    parts.append('</div>')

    # 子项均值（单独一行，不再跟连板分布并排）
    sub_cols = ["concept_leader_score", "early_seal_score", "blow_up_proxy",
                "position_60d", "lhb_hotmoney"]
    sub_cols = [c for c in sub_cols if c in df.columns]
    if sub_cols:
        parts.append('<h3>子项均值（全样本）</h3><table>')
        parts.append('<tr><th>子项</th><th>均值</th><th>标准差</th><th>非空率</th></tr>')
        for c in sub_cols:
            parts.append(f'<tr><td>{c}</td><td>{df[c].mean():.4f}</td><td>{df[c].std():.4f}</td><td>{df[c].notna().mean()*100:.1f}%</td></tr>')
        parts.append('</table>')

    # 最新 buy 信号
    latest_date = df["trade_date"].max()
    latest_buy = df[(df["trade_date"] == latest_date) & (df["signal"] == "buy")].sort_values("rank").head(10)
    if len(latest_buy):
        parts.append(f'<h3>最新 buy 信号 ({latest_date.date()})</h3><table>')
        cols = ["ts_code", "name", "limit_up_streak", "score", "rank", "confidence"]
        cols = [c for c in cols if c in latest_buy.columns]
        parts.append("<tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>")
        for _, r in latest_buy.iterrows():
            parts.append("<tr>" + "".join(f"<td>{r[c]}</td>" for c in cols) + "</tr>")
        parts.append('</table>')

    parts.append('</section>')
    return "\n".join(parts)


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

    hero = render_hero(factor_reports or [], strategy_scenarios or [])
    charts = render_charts_section(factor_reports, strategy_scenarios)
    factor_sec = render_factor_section(factor_reports)
    strategy_sec = render_strategy_section(strategy_scenarios)
    signal_sec = render_signal_section(parquet_path)
    charts_script = render_charts_script(factor_reports, strategy_scenarios)

    css = """
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
           margin: 0; padding: 32px 24px; background: #f8fafc; color: #1e293b; line-height: 1.6; }
    .container { max-width: 1280px; margin: 0 auto; }
    h1 { font-size: 28px; margin: 0 0 8px; color: #0f172a; }
    h2 { font-size: 22px; margin: 32px 0 16px; padding-bottom: 8px; border-bottom: 2px solid #e2e8f0; color: #0f172a; }
    h3 { font-size: 18px; margin: 20px 0 12px; color: #334155; }
    h4 { font-size: 15px; margin: 16px 0 8px; color: #475569; }
    .header { background: linear-gradient(135deg, #1e293b 0%, #334155 100%); color: white;
              padding: 24px; border-radius: 12px; margin-bottom: 32px; }
    .header h1 { color: white; }
    .header .sub { opacity: 0.85; font-size: 14px; }
    .metric-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
                   gap: 12px; margin: 24px 0; }
    .metric-card { background: white; padding: 16px; border-radius: 8px;
                   box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    .metric-title { font-size: 12px; color: #64748b; margin-bottom: 4px; }
    .metric-value { font-size: 22px; font-weight: 700; }
    section { background: white; padding: 24px; border-radius: 12px; margin: 24px 0;
              box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
    .hint { color: #64748b; font-size: 13px; margin: 0 0 16px; }
    .meta { color: #64748b; font-size: 13px; }
    code { background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
    table { width: 100%; border-collapse: collapse; margin: 8px 0 16px; font-size: 14px; }
    th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }
    th { background: #f8fafc; font-weight: 600; color: #475569; }
    tr:hover { background: #f8fafc; }
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
    @media (max-width: 768px) { .two-col { grid-template-columns: 1fr; } }
    .chart-container { margin: 24px 0; padding: 16px; background: #fafafa; border-radius: 8px; }
    .chart-container h4 { margin-top: 0; }
    .spread { background: linear-gradient(90deg, #fef3c7 0%, transparent 100%);
              padding: 8px 12px; border-radius: 6px; margin: 12px 0; }
    """

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Alpha-A3 回测报告</title>
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

  {hero}

  {charts}

  {signal_sec}

  {factor_sec}

  {strategy_sec}

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
    ap = argparse.ArgumentParser(description="A3 回测 HTML 可视化（升级版）")
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
