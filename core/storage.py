"""
SQLite 기반 거래 기록 및 계좌 상태 저장소.
백테스트와 실시간 봇이 공유. WAL 모드, threading.Lock 쓰기보호.
"""

import json
import logging
import math
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from core.paper_wallet import (
    AccountState,
    CloseResult,
    ExecutionResult,
    OpenResult,
    Position,
    PositionSide,
)

logger = logging.getLogger(__name__)

# KST = UTC+9
_KST = timezone(timedelta(hours=9))


def _now_kst_iso() -> str:
    return datetime.now(_KST).isoformat(timespec="seconds")


def _ts_to_kst_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=_KST).isoformat(timespec="seconds")


class Storage:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def initialize(self) -> None:
        """PRAGMA 설정 + 테이블 생성."""
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;

                CREATE TABLE IF NOT EXISTS trades (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp        REAL    NOT NULL,
                    timestamp_kst    TEXT    NOT NULL,
                    symbol           TEXT    NOT NULL,
                    side             TEXT    NOT NULL,
                    action           TEXT    NOT NULL,
                    trigger          TEXT    NOT NULL,
                    strategy_name    TEXT    NOT NULL,
                    leverage         INTEGER NOT NULL DEFAULT 1,
                    mark_price       REAL    NOT NULL,
                    filled_price     REAL    NOT NULL,
                    quantity         REAL    NOT NULL,
                    notional         REAL    NOT NULL,
                    fee_usdt         REAL    NOT NULL,
                    realized_pnl     REAL    NOT NULL DEFAULT 0.0,
                    margin           REAL    NOT NULL,
                    is_liquidation   INTEGER NOT NULL DEFAULT 0,
                    balance_after    REAL    NOT NULL,
                    equity_after     REAL    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS equity_curve (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp      REAL    NOT NULL,
                    timestamp_kst  TEXT    NOT NULL,
                    total_equity   REAL    NOT NULL,
                    balance_usdt   REAL    NOT NULL,
                    used_margin    REAL    NOT NULL,
                    unrealized_pnl REAL    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS signals (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp      REAL    NOT NULL,
                    timestamp_kst  TEXT    NOT NULL,
                    symbol         TEXT    NOT NULL,
                    strategy_name  TEXT    NOT NULL,
                    action         TEXT    NOT NULL,
                    price          REAL    NOT NULL,
                    indicator_json TEXT    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS account_state (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp      REAL    NOT NULL,
                    timestamp_kst  TEXT    NOT NULL,
                    balance_usdt   REAL    NOT NULL,
                    positions_json TEXT    NOT NULL,
                    realized_pnl   REAL    NOT NULL,
                    mode           TEXT    NOT NULL DEFAULT 'paper'
                );
            """)
            self._conn.commit()

    # ── 거래 기록 ──────────────────────────────────────────────

    def record_trade(
        self,
        result: ExecutionResult,
        symbol: str,
        trigger: str,
        strategy_name: str,
        mark_price: float,
        equity_after: float,
    ) -> None:
        if not result.success or result.detail is None:
            return

        detail = result.detail
        ts = time.time()

        if isinstance(detail, OpenResult):
            side = detail.side.value
            filled_price = detail.filled_price
            quantity = detail.quantity
            notional = detail.notional
            fee_usdt = detail.fee_usdt
            realized_pnl = 0.0
            margin = detail.margin_usdt
            is_liq = 0
            # leverage: OpenResult에 직접 없으므로 notional/margin에서 역산
            leverage = round(detail.notional / detail.margin_usdt) if detail.margin_usdt > 0 else 1
        elif isinstance(detail, CloseResult):
            side = detail.side.value
            filled_price = detail.filled_price
            quantity = detail.quantity
            notional = detail.quantity * detail.filled_price
            fee_usdt = detail.fee_usdt
            realized_pnl = detail.realized_pnl
            margin = detail.returned_margin
            is_liq = 1 if detail.is_liquidation else 0
            leverage = 1  # close 시 leverage 정보 없음 — 기본값
        else:
            return

        # balance_after: equity_after - unrealized(알 수 없음) 대신 직접 넘어온 equity 사용
        # balance_after 는 별도로 전달하지 않아 equity_after 로 대체 (호출자가 같이 넘기면 이상적이나 계약상 없음)
        balance_after = equity_after  # 호출 시점 equity 로 근사

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO trades
                  (timestamp, timestamp_kst, symbol, side, action, trigger, strategy_name,
                   leverage, mark_price, filled_price, quantity, notional, fee_usdt,
                   realized_pnl, margin, is_liquidation, balance_after, equity_after)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts, _ts_to_kst_iso(ts), symbol, side, result.action, trigger,
                    strategy_name, leverage, mark_price, filled_price, quantity,
                    notional, fee_usdt, realized_pnl, margin, is_liq,
                    balance_after, equity_after,
                ),
            )
            self._conn.commit()

    def record_equity(
        self,
        total_equity: float,
        balance_usdt: float,
        used_margin: float,
        unrealized_pnl: float,
    ) -> None:
        ts = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO equity_curve
                  (timestamp, timestamp_kst, total_equity, balance_usdt, used_margin, unrealized_pnl)
                VALUES (?,?,?,?,?,?)
                """,
                (ts, _ts_to_kst_iso(ts), total_equity, balance_usdt, used_margin, unrealized_pnl),
            )
            self._conn.commit()

    def record_signal(
        self,
        symbol: str,
        strategy_name: str,
        action: str,
        price: float,
        indicators: dict,
    ) -> None:
        ts = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO signals
                  (timestamp, timestamp_kst, symbol, strategy_name, action, price, indicator_json)
                VALUES (?,?,?,?,?,?,?)
                """,
                (ts, _ts_to_kst_iso(ts), symbol, strategy_name, action, price,
                 json.dumps(indicators)),
            )
            self._conn.commit()

    # ── 계좌 상태 저장/복원 ────────────────────────────────────

    def save_account_state(self, state: AccountState, mode: str = "paper") -> None:
        positions_dict: dict[str, Any] = {}
        for sym, pos in state.positions.items():
            positions_dict[sym] = {
                "symbol": pos.symbol,
                "side": pos.side.value,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "leverage": pos.leverage,
                "margin_usdt": pos.margin_usdt,
                "liquidation_price": pos.liquidation_price,
            }

        ts = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO account_state
                  (timestamp, timestamp_kst, balance_usdt, positions_json, realized_pnl, mode)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    ts, _ts_to_kst_iso(ts),
                    state.balance_usdt,
                    json.dumps(positions_dict),
                    state.realized_pnl,
                    mode,
                ),
            )
            self._conn.commit()

    def load_account_state(self) -> "AccountState | None":
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM account_state ORDER BY id DESC LIMIT 1"
            ).fetchone()

        if row is None:
            return None

        positions: dict[str, Position] = {}
        raw = json.loads(row["positions_json"])
        for sym, d in raw.items():
            positions[sym] = Position(
                symbol=d["symbol"],
                side=PositionSide(d["side"]),
                quantity=d["quantity"],
                entry_price=d["entry_price"],
                leverage=d["leverage"],
                margin_usdt=d["margin_usdt"],
                liquidation_price=d["liquidation_price"],
            )

        return AccountState(
            balance_usdt=row["balance_usdt"],
            positions=positions,
            realized_pnl=row["realized_pnl"],
            created_at=row["timestamp"],
        )

    # ── 조회 ──────────────────────────────────────────────────

    def get_trades(
        self,
        symbol: "str | None" = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        if symbol:
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE symbol=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (symbol, limit, offset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_equity_curve(
        self,
        since_ts: "float | None" = None,
        limit: int = 2000,
    ) -> list[dict]:
        if since_ts is not None:
            rows = self._conn.execute(
                "SELECT * FROM equity_curve WHERE timestamp>=? ORDER BY id ASC LIMIT ?",
                (since_ts, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM equity_curve ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_performance_report(self) -> dict:
        """trades + equity_curve 기반 성과 지표. 빈 DB 에서도 안전."""
        trades_rows = self._conn.execute("SELECT * FROM trades ORDER BY id ASC").fetchall()
        equity_rows = self._conn.execute(
            "SELECT total_equity FROM equity_curve ORDER BY id ASC"
        ).fetchall()

        trade_count = len(trades_rows)
        num_liquidations = sum(1 for r in trades_rows if r["is_liquidation"])
        realized_pnl_usdt = sum(r["realized_pnl"] for r in trades_rows)

        # 승/패 구분 (CLOSE 체결만, realized_pnl 기준)
        close_trades = [r for r in trades_rows if r["action"] == "CLOSE"]
        wins = [r for r in close_trades if r["realized_pnl"] > 0]
        losses = [r for r in close_trades if r["realized_pnl"] <= 0]
        win_rate_pct = (len(wins) / len(close_trades) * 100) if close_trades else 0.0
        avg_win_usdt = (sum(r["realized_pnl"] for r in wins) / len(wins)) if wins else 0.0
        avg_loss_usdt = (sum(r["realized_pnl"] for r in losses) / len(losses)) if losses else 0.0

        gross_win = sum(r["realized_pnl"] for r in wins)
        gross_loss = abs(sum(r["realized_pnl"] for r in losses))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 0.0

        # equity curve 기반 지표
        equities = [r["total_equity"] for r in equity_rows]

        total_return_pct = 0.0
        mdd_pct = 0.0
        sharpe_ratio = 0.0
        daily_return_pct = 0.0
        unrealized_pnl_usdt = 0.0

        if equities:
            start_eq = equities[0]
            end_eq = equities[-1]
            total_return_pct = ((end_eq - start_eq) / start_eq * 100) if start_eq > 0 else 0.0

            # MDD
            cum_max = equities[0]
            max_dd = 0.0
            for eq in equities:
                if eq > cum_max:
                    cum_max = eq
                dd = (eq - cum_max) / cum_max * 100 if cum_max > 0 else 0.0
                if dd < max_dd:
                    max_dd = dd
            mdd_pct = max_dd  # 음수

            # Sharpe (단순 equity 수익률 기준, sqrt(252) annualize)
            if len(equities) >= 2:
                rets = [
                    (equities[i] - equities[i - 1]) / equities[i - 1]
                    for i in range(1, len(equities))
                    if equities[i - 1] > 0
                ]
                if len(rets) >= 2:
                    mean_r = sum(rets) / len(rets)
                    var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
                    std_r = math.sqrt(var_r) if var_r > 0 else 0.0
                    sharpe_ratio = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0

            # 일일 수익률: 총 수익률 / 기간(일). equity_curve timestamp 기반
            eq_ts_rows = self._conn.execute(
                "SELECT timestamp FROM equity_curve ORDER BY id ASC LIMIT 1"
            ).fetchone()
            eq_ts_last = self._conn.execute(
                "SELECT timestamp FROM equity_curve ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if eq_ts_rows and eq_ts_last:
                days = max((eq_ts_last["timestamp"] - eq_ts_rows["timestamp"]) / 86400, 1.0)
                daily_return_pct = total_return_pct / days

            # 미실현 손익: 최근 equity_curve 의 unrealized_pnl
            last_eq_row = self._conn.execute(
                "SELECT unrealized_pnl FROM equity_curve ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if last_eq_row:
                unrealized_pnl_usdt = last_eq_row["unrealized_pnl"]

        return {
            "total_return_pct": total_return_pct,
            "daily_return_pct": daily_return_pct,
            "trade_count": trade_count,
            "win_rate_pct": win_rate_pct,
            "avg_win_usdt": avg_win_usdt,
            "avg_loss_usdt": avg_loss_usdt,
            "profit_factor": profit_factor,
            "mdd_pct": mdd_pct,
            "sharpe_ratio": sharpe_ratio,
            "num_liquidations": num_liquidations,
            "realized_pnl_usdt": realized_pnl_usdt,
            "unrealized_pnl_usdt": unrealized_pnl_usdt,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
