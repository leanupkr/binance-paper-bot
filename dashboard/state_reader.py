"""
dashboard/state_reader.py — SQLite 읽기 전용 StateReader.
봇이 쓰는 Storage 와 같은 DB 를 WAL 모드 읽기 전용으로 열어 조회.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Protocol, runtime_checkable

from core.config import AppConfig


@runtime_checkable
class StateReader(Protocol):
    def get_account_state(self) -> dict: ...
    def get_equity(self) -> float: ...
    def get_trades(self, limit: int = 100, offset: int = 0, symbol: str | None = None) -> list[dict]: ...
    def get_equity_curve(self, since_hours: float = 24.0) -> list[dict]: ...
    def get_performance_report(self) -> dict: ...


class SQLiteStateReader:
    """읽기 전용 SQLite 연결. WAL 모드이므로 봇 쓰기와 충돌 없이 동시 읽기 가능."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # immutable=1: 쓰기 없음 보장(WAL 환경에서 안전)
        uri = f"file:{db_path}?mode=ro"
        try:
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        except sqlite3.OperationalError:
            # DB 파일이 아직 없으면 일반 연결(빈 메모리 DB 아님 — 실제 경로 체크)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    # ── 계좌 상태 ──────────────────────────────────────────────

    def get_account_state(self) -> dict:
        """잔고/포지션 목록/평가자산/used_margin/last_tick/mode 반환."""
        try:
            row = self._conn.execute(
                "SELECT * FROM account_state ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None

        if row is None:
            return {
                "balance_usdt": 0.0,
                "positions": {},
                "realized_pnl": 0.0,
                "used_margin": 0.0,
                "equity": 0.0,
                "last_tick": None,
                "mode": "paper",
            }

        positions_raw: dict = json.loads(row["positions_json"] or "{}")

        # equity_curve 최신 row 에서 equity/used_margin 취득
        eq_row = None
        try:
            eq_row = self._conn.execute(
                "SELECT total_equity, used_margin, unrealized_pnl FROM equity_curve ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            pass

        equity = eq_row["total_equity"] if eq_row else row["balance_usdt"]
        used_margin = eq_row["used_margin"] if eq_row else 0.0

        return {
            "balance_usdt": row["balance_usdt"],
            "positions": positions_raw,
            "realized_pnl": row["realized_pnl"],
            "used_margin": used_margin,
            "equity": equity,
            "last_tick": row["timestamp"],
            "mode": row["mode"],
        }

    # ── 단순 equity ────────────────────────────────────────────

    def get_equity(self) -> float:
        try:
            row = self._conn.execute(
                "SELECT total_equity FROM equity_curve ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return 0.0
        return float(row["total_equity"]) if row else 0.0

    # ── 거래 목록 ──────────────────────────────────────────────

    def get_trades(
        self,
        limit: int = 100,
        offset: int = 0,
        symbol: str | None = None,
    ) -> list[dict]:
        try:
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
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]

    # ── 자산 곡선 ──────────────────────────────────────────────

    def get_equity_curve(self, since_hours: float = 24.0) -> list[dict]:
        since_ts = time.time() - since_hours * 3600
        try:
            rows = self._conn.execute(
                "SELECT timestamp, timestamp_kst, total_equity, balance_usdt, used_margin, unrealized_pnl "
                "FROM equity_curve WHERE timestamp >= ? ORDER BY id ASC LIMIT 2000",
                (since_ts,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]

    # ── 성과 리포트 ────────────────────────────────────────────

    def get_performance_report(self) -> dict:
        """Storage.get_performance_report 와 동일 쿼리 로직."""
        import math

        _empty = {
            "total_return_pct": 0.0,
            "daily_return_pct": 0.0,
            "trade_count": 0,
            "win_rate_pct": 0.0,
            "avg_win_usdt": 0.0,
            "avg_loss_usdt": 0.0,
            "profit_factor": 0.0,
            "mdd_pct": 0.0,
            "sharpe_ratio": 0.0,
            "num_liquidations": 0,
            "realized_pnl_usdt": 0.0,
            "unrealized_pnl_usdt": 0.0,
        }

        try:
            trades_rows = self._conn.execute(
                "SELECT action, realized_pnl, is_liquidation FROM trades ORDER BY id ASC"
            ).fetchall()
            equity_rows = self._conn.execute(
                "SELECT total_equity, timestamp FROM equity_curve ORDER BY id ASC"
            ).fetchall()
        except sqlite3.OperationalError:
            return _empty

        trade_count = len(trades_rows)
        num_liquidations = sum(1 for r in trades_rows if r["is_liquidation"])
        realized_pnl_usdt = sum(r["realized_pnl"] for r in trades_rows)

        close_trades = [r for r in trades_rows if r["action"] == "CLOSE"]
        wins = [r for r in close_trades if r["realized_pnl"] > 0]
        losses = [r for r in close_trades if r["realized_pnl"] <= 0]
        win_rate_pct = (len(wins) / len(close_trades) * 100) if close_trades else 0.0
        avg_win_usdt = (sum(r["realized_pnl"] for r in wins) / len(wins)) if wins else 0.0
        avg_loss_usdt = (sum(r["realized_pnl"] for r in losses) / len(losses)) if losses else 0.0
        gross_win = sum(r["realized_pnl"] for r in wins)
        gross_loss = abs(sum(r["realized_pnl"] for r in losses))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 0.0

        equities = [r["total_equity"] for r in equity_rows]
        timestamps = [r["timestamp"] for r in equity_rows]

        if not equities:
            return {**_empty, "trade_count": trade_count, "num_liquidations": num_liquidations,
                    "realized_pnl_usdt": realized_pnl_usdt, "win_rate_pct": win_rate_pct,
                    "avg_win_usdt": avg_win_usdt, "avg_loss_usdt": avg_loss_usdt,
                    "profit_factor": profit_factor}

        start_eq = equities[0]
        end_eq = equities[-1]
        total_return_pct = ((end_eq - start_eq) / start_eq * 100) if start_eq > 0 else 0.0

        cum_max = equities[0]
        max_dd = 0.0
        for eq in equities:
            if eq > cum_max:
                cum_max = eq
            dd = (eq - cum_max) / cum_max * 100 if cum_max > 0 else 0.0
            if dd < max_dd:
                max_dd = dd
        mdd_pct = max_dd

        sharpe_ratio = 0.0
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

        days = max((timestamps[-1] - timestamps[0]) / 86400, 1.0) if len(timestamps) >= 2 else 1.0
        daily_return_pct = total_return_pct / days

        try:
            last_eq_row = self._conn.execute(
                "SELECT unrealized_pnl FROM equity_curve ORDER BY id DESC LIMIT 1"
            ).fetchone()
            unrealized_pnl_usdt = float(last_eq_row["unrealized_pnl"]) if last_eq_row else 0.0
        except sqlite3.OperationalError:
            unrealized_pnl_usdt = 0.0

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


def create_state_reader(config: AppConfig) -> StateReader:
    """env STATE_BACKEND 기본 sqlite. 향후 다른 백엔드 확장 가능."""
    import os
    backend = os.environ.get("STATE_BACKEND", "sqlite")
    if backend == "sqlite":
        return SQLiteStateReader(config.db_path)
    raise ValueError(f"지원하지 않는 STATE_BACKEND: {backend}")
