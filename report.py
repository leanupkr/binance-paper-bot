"""
report.py — CLI + HTML 성과 리포트
사용법:
  python report.py [--config config.yaml] [--format text|html] [--output reports/report.html]
"""
from __future__ import annotations

import argparse
import base64
import io
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))


# ──────────────────────────────────────────────────────────────
# 텍스트 리포트
# ──────────────────────────────────────────────────────────────

def _fmt(val: Any, fmt_str: str = ".2f", suffix: str = "") -> str:
    try:
        return f"{val:{fmt_str}}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def print_text_report(perf: dict, trades: list[dict]) -> None:
    sep = "=" * 52
    print(sep)
    print("  PAPER TRADING PERFORMANCE REPORT")
    print(f"  Generated: {datetime.now(_KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    print(sep)

    rows = [
        ("Total Return",        _fmt(perf.get("total_return_pct", 0.0), ".2f", " %")),
        ("Daily Return (avg)",  _fmt(perf.get("daily_return_pct", 0.0), ".4f", " %")),
        ("Realized PnL",        _fmt(perf.get("realized_pnl_usdt", 0.0), ".4f", " USDT")),
        ("Unrealized PnL",      _fmt(perf.get("unrealized_pnl_usdt", 0.0), ".4f", " USDT")),
        ("Trade Count",         str(perf.get("trade_count", 0))),
        ("Win Rate",            _fmt(perf.get("win_rate_pct", 0.0), ".1f", " %")),
        ("Avg Win",             _fmt(perf.get("avg_win_usdt", 0.0), ".4f", " USDT")),
        ("Avg Loss",            _fmt(perf.get("avg_loss_usdt", 0.0), ".4f", " USDT")),
        ("Profit Factor",       _fmt(perf.get("profit_factor", 0.0), ".3f")),
        ("MDD",                 _fmt(perf.get("mdd_pct", 0.0), ".2f", " %")),
        ("Sharpe Ratio",        _fmt(perf.get("sharpe_ratio", 0.0), ".3f")),
        ("Liquidations",        str(perf.get("num_liquidations", 0))),
    ]

    col_w = 26
    for label, value in rows:
        print(f"  {label:<{col_w}} {value}")

    print(sep)

    if trades:
        print(f"\n  Recent Trades (last {min(len(trades), 10)})")
        print(f"  {'#':<4} {'Symbol':<12} {'Side':<6} {'Action':<6} {'Lev':>4} {'Fill Price':>12} {'Realized PnL':>13} {'Liq':>4}")
        print("  " + "-" * 66)
        for i, t in enumerate(trades[:10], 1):
            liq_mark = "  Y" if t.get("is_liquidation") else "  -"
            print(
                f"  {i:<4} "
                f"{t.get('symbol',''):<12} "
                f"{t.get('side',''):<6} "
                f"{t.get('action',''):<6} "
                f"{t.get('leverage',1):>4}x "
                f"{t.get('filled_price',0.0):>12.4f} "
                f"{t.get('realized_pnl',0.0):>+13.4f} "
                f"{liq_mark}"
            )
    else:
        print("\n  No trades recorded yet.")

    print(sep)


# ──────────────────────────────────────────────────────────────
# Equity Curve PNG → base64
# ──────────────────────────────────────────────────────────────

def _build_equity_chart_b64(equity_curve: list[dict]) -> str | None:
    """matplotlib로 equity curve PNG 생성 후 base64 반환. 데이터 없으면 None."""
    if not equity_curve:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        logger.warning("matplotlib not available; skipping chart")
        return None

    timestamps = []
    equities = []
    for row in equity_curve:
        ts = row.get("timestamp")
        eq = row.get("total_equity")
        if ts is not None and eq is not None:
            timestamps.append(datetime.fromtimestamp(float(ts), tz=_KST))
            equities.append(float(eq))

    if len(equities) < 2:
        return None

    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#0f1117")
    ax.set_facecolor("#0f1117")

    # 수익/손실 구간 음영
    start_eq = equities[0]
    colors = ["#22c55e" if e >= start_eq else "#ef4444" for e in equities]
    for i in range(1, len(equities)):
        ax.fill_between(
            timestamps[i - 1 : i + 1],
            start_eq,
            equities[i - 1 : i + 1],
            color="#22c55e" if equities[i] >= start_eq else "#ef4444",
            alpha=0.15,
        )

    ax.plot(timestamps, equities, color="#60a5fa", linewidth=1.5, zorder=3)
    ax.axhline(start_eq, color="#6b7280", linewidth=0.8, linestyle="--", alpha=0.6)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    fig.autofmt_xdate(rotation=30)

    for spine in ax.spines.values():
        spine.set_color("#374151")
    ax.tick_params(colors="#9ca3af", labelsize=8)
    ax.yaxis.set_label_text("Equity (USDT)", color="#9ca3af", fontsize=9)
    ax.set_title("Equity Curve", color="#e5e7eb", fontsize=11, pad=10)
    ax.grid(color="#1f2937", linewidth=0.5, alpha=0.8)

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


# ──────────────────────────────────────────────────────────────
# HTML 리포트
# ──────────────────────────────────────────────────────────────

def _metric_card(label: str, value: str, color: str = "#e5e7eb") -> str:
    return f"""
      <div class="card">
        <div class="card-label">{label}</div>
        <div class="card-value" style="color:{color};">{value}</div>
      </div>"""


def _side_badge(side: str) -> str:
    cls = "badge-long" if side.upper() == "LONG" else "badge-short"
    return f'<span class="badge {cls}">{side}</span>'


def _bool_cell(val: Any) -> str:
    if val:
        return '<span style="color:#ef4444;">Y</span>'
    return '<span style="color:#6b7280;">-</span>'


def build_html_report(
    perf: dict,
    trades: list[dict],
    equity_curve: list[dict],
) -> str:
    chart_b64 = _build_equity_chart_b64(equity_curve)
    chart_section = (
        f'<img src="data:image/png;base64,{chart_b64}" class="chart-img" alt="Equity Curve">'
        if chart_b64
        else '<p class="no-data">Insufficient data to render chart.</p>'
    )

    # 지표 카드 색상
    ret_pct = perf.get("total_return_pct", 0.0)
    ret_color = "#22c55e" if ret_pct >= 0 else "#ef4444"
    mdd_color = "#ef4444" if perf.get("mdd_pct", 0.0) < 0 else "#6b7280"
    sharpe_val = perf.get("sharpe_ratio", 0.0)
    sharpe_color = "#22c55e" if sharpe_val >= 1 else ("#f59e0b" if sharpe_val >= 0 else "#ef4444")

    cards = (
        _metric_card("Total Return", _fmt(perf.get("total_return_pct", 0.0), ".2f", " %"), ret_color)
        + _metric_card("Realized PnL", _fmt(perf.get("realized_pnl_usdt", 0.0), ".4f", " USDT"),
                       "#22c55e" if perf.get("realized_pnl_usdt", 0.0) >= 0 else "#ef4444")
        + _metric_card("Unrealized PnL", _fmt(perf.get("unrealized_pnl_usdt", 0.0), ".4f", " USDT"))
        + _metric_card("Trade Count", str(perf.get("trade_count", 0)))
        + _metric_card("Win Rate", _fmt(perf.get("win_rate_pct", 0.0), ".1f", " %"),
                       "#22c55e" if perf.get("win_rate_pct", 0.0) >= 50 else "#f59e0b")
        + _metric_card("Avg Win", _fmt(perf.get("avg_win_usdt", 0.0), ".4f", " USDT"), "#22c55e")
        + _metric_card("Avg Loss", _fmt(perf.get("avg_loss_usdt", 0.0), ".4f", " USDT"), "#ef4444")
        + _metric_card("Profit Factor", _fmt(perf.get("profit_factor", 0.0), ".3f"),
                       "#22c55e" if perf.get("profit_factor", 0.0) >= 1 else "#ef4444")
        + _metric_card("MDD", _fmt(perf.get("mdd_pct", 0.0), ".2f", " %"), mdd_color)
        + _metric_card("Sharpe Ratio", _fmt(perf.get("sharpe_ratio", 0.0), ".3f"), sharpe_color)
        + _metric_card("Liquidations", str(perf.get("num_liquidations", 0)),
                       "#ef4444" if perf.get("num_liquidations", 0) > 0 else "#6b7280")
        + _metric_card("Daily Return", _fmt(perf.get("daily_return_pct", 0.0), ".4f", " %"))
    )

    # 거래 테이블 행
    if trades:
        trade_rows_html = ""
        for t in trades:
            liq = bool(t.get("is_liquidation"))
            side_str = t.get("side", "")
            rpnl = t.get("realized_pnl", 0.0)
            rpnl_color = "#22c55e" if rpnl > 0 else ("#ef4444" if rpnl < 0 else "#6b7280")
            trade_rows_html += f"""
          <tr>
            <td>{t.get('timestamp_kst', '')}</td>
            <td>{t.get('symbol','')}</td>
            <td>{_side_badge(side_str)}</td>
            <td>{t.get('action','')}</td>
            <td>{t.get('leverage',1)}x</td>
            <td>{t.get('mark_price',0.0):.4f}</td>
            <td>{t.get('filled_price',0.0):.4f}</td>
            <td>{t.get('quantity',0.0):.6f}</td>
            <td style="color:{rpnl_color};">{rpnl:+.4f}</td>
            <td>{t.get('fee_usdt',0.0):.4f}</td>
            <td>{_bool_cell(liq)}</td>
            <td>{t.get('trigger','')}</td>
          </tr>"""
    else:
        trade_rows_html = '<tr><td colspan="12" class="no-data">No trades recorded yet.</td></tr>'

    generated_at = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S KST")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Paper Trading Report</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f1117;
      color: #e5e7eb;
      font-size: 14px;
      line-height: 1.5;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 32px 20px; }}
    h1 {{ font-size: 22px; font-weight: 600; color: #f9fafb; margin-bottom: 4px; }}
    .subtitle {{ color: #6b7280; font-size: 12px; margin-bottom: 28px; }}
    h2 {{ font-size: 15px; font-weight: 600; color: #d1d5db; margin: 28px 0 14px; }}

    /* Metric cards */
    .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }}
    .card {{
      background: #1a1d27;
      border: 1px solid #1f2937;
      border-radius: 8px;
      padding: 14px 16px;
    }}
    .card-label {{ font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 6px; }}
    .card-value {{ font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }}

    /* Chart */
    .chart-wrap {{ background: #1a1d27; border: 1px solid #1f2937; border-radius: 8px; padding: 16px; overflow: hidden; }}
    .chart-img {{ width: 100%; height: auto; display: block; }}

    /* Table */
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    thead tr {{ background: #1f2937; }}
    th {{ padding: 8px 10px; text-align: left; color: #9ca3af; font-weight: 500; white-space: nowrap; }}
    tbody tr {{ border-bottom: 1px solid #1f2937; }}
    tbody tr:hover {{ background: #1a1d27; }}
    td {{ padding: 7px 10px; white-space: nowrap; font-variant-numeric: tabular-nums; }}

    .badge {{ display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
    .badge-long {{ background: rgba(34,197,94,0.15); color: #22c55e; }}
    .badge-short {{ background: rgba(239,68,68,0.15); color: #ef4444; }}

    .no-data {{ color: #4b5563; text-align: center; padding: 24px; }}
    footer {{ margin-top: 40px; color: #374151; font-size: 11px; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Paper Trading Performance Report</h1>
    <div class="subtitle">Generated: {generated_at} &nbsp;|&nbsp; Binance USDT-M Futures (Paper)</div>

    <h2>Metrics</h2>
    <div class="cards">{cards}
    </div>

    <h2>Equity Curve</h2>
    <div class="chart-wrap">
      {chart_section}
    </div>

    <h2>Recent Trades</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time (KST)</th>
            <th>Symbol</th>
            <th>Side</th>
            <th>Action</th>
            <th>Lev</th>
            <th>Mark Price</th>
            <th>Fill Price</th>
            <th>Qty</th>
            <th>Realized PnL</th>
            <th>Fee (USDT)</th>
            <th>Liq</th>
            <th>Trigger</th>
          </tr>
        </thead>
        <tbody>
          {trade_rows_html}
        </tbody>
      </table>
    </div>

    <footer>Binance USDT-M Paper Trading Bot &mdash; Simulation Only</footer>
  </div>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────
# CLI 진입점
# ──────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Paper trading performance report")
    parser.add_argument("--config", default="config.yaml", help="config.yaml 경로")
    parser.add_argument("--format", choices=["text", "html"], default="text",
                        dest="fmt", help="출력 형식 (default: text)")
    parser.add_argument("--output", default="reports/report.html",
                        help="HTML 출력 경로 (--format html 시 사용)")
    args = parser.parse_args()

    # config 로드
    try:
        from core.config import load_config
        cfg = load_config(args.config)
        db_path = cfg.db_path
    except Exception as e:
        logger.error("config 로드 실패: %s", e)
        sys.exit(1)

    # Storage 조회
    try:
        from core.storage import Storage
        storage = Storage(db_path)
        storage.initialize()

        perf = storage.get_performance_report()
        trades = storage.get_trades(limit=50)
        equity_curve = storage.get_equity_curve()
        storage.close()
    except Exception as e:
        logger.error("DB 조회 실패: %s", e)
        sys.exit(1)

    if args.fmt == "text":
        print_text_report(perf, trades)
    else:
        html = build_html_report(perf, trades, equity_curve)
        out_path = args.output
        os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("HTML report saved: %s", out_path)
        print(f"Report saved: {out_path}")


if __name__ == "__main__":
    main()
