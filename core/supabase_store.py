"""
core/supabase_store.py — Supabase 기반 저장소 (REST / PostgREST).

core/storage.py 의 Storage 와 완전히 동일한 공개 인터페이스를 Supabase REST API 로 구현.
psycopg2(직접 Postgres) 대신 REST 를 쓰는 이유:
 - GitHub Actions 러너는 IPv4 전용인데 Supabase 직접 연결은 IPv6 → 풀러 의존.
 - REST(https)는 IPv4 에서 안정적이고, 자격증명이 service_role 키 1개로 단순.
활성화: STORAGE_BACKEND=supabase + SUPABASE_URL + SUPABASE_SERVICE_KEY (RLS 우회 쓰기).
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from core.paper_wallet import (
    AccountState,
    CloseResult,
    ExecutionResult,
    OpenResult,
    Position,
    PositionSide,
)

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))


def _ts_to_kst_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=_KST).isoformat(timespec="seconds")


class SupabaseStorage:
    """Supabase REST(PostgREST) 백엔드. Storage 와 동일한 공개 인터페이스."""

    def __init__(self, url: str, service_key: str) -> None:
        self._base = url.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }
        self._session = requests.Session()
        self._session.headers.update(self._headers)

    # ── 내부 헬퍼 ─────────────────────────────────────────────
    def _insert(self, table: str, row: dict) -> None:
        """단일 행 INSERT (return=minimal). 실패해도 예외 전파하지 않음(봇 생존)."""
        try:
            resp = self._session.post(
                f"{self._base}/{table}",
                json=row,
                headers={"Prefer": "return=minimal"},
                timeout=10,
            )
            if resp.status_code >= 300:
                logger.error("Supabase INSERT %s 실패 %s: %s", table, resp.status_code, resp.text[:300])
        except Exception:
            logger.exception("Supabase INSERT %s 예외", table)

    def _select(self, table: str, params: dict) -> list[dict]:
        try:
            resp = self._session.get(f"{self._base}/{table}", params=params, timeout=10)
            if resp.status_code >= 300:
                logger.error("Supabase SELECT %s 실패 %s: %s", table, resp.status_code, resp.text[:300])
                return []
            return resp.json()
        except Exception:
            logger.exception("Supabase SELECT %s 예외", table)
            return []

    def initialize(self) -> None:
        """스키마는 마이그레이션(supabase/schema.sql)으로 이미 생성됨. REST 백엔드는 no-op."""
        logger.info("SupabaseStorage(REST) 사용 — 스키마는 마이그레이션으로 관리됨")

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
            leverage = 1
        else:
            return

        self._insert("trades", {
            "timestamp": ts,
            "timestamp_kst": _ts_to_kst_iso(ts),
            "symbol": symbol,
            "side": side,
            "action": result.action,
            "trigger": trigger,
            "strategy_name": strategy_name,
            "leverage": leverage,
            "mark_price": mark_price,
            "filled_price": filled_price,
            "quantity": quantity,
            "notional": notional,
            "fee_usdt": fee_usdt,
            "realized_pnl": realized_pnl,
            "margin": margin,
            "is_liquidation": is_liq,
            "balance_after": equity_after,
            "equity_after": equity_after,
        })

    def record_equity(
        self,
        total_equity: float,
        balance_usdt: float,
        used_margin: float,
        unrealized_pnl: float,
    ) -> None:
        ts = time.time()
        self._insert("equity_curve", {
            "timestamp": ts,
            "timestamp_kst": _ts_to_kst_iso(ts),
            "total_equity": total_equity,
            "balance_usdt": balance_usdt,
            "used_margin": used_margin,
            "unrealized_pnl": unrealized_pnl,
        })

    def record_signal(
        self,
        symbol: str,
        strategy_name: str,
        action: str,
        price: float,
        indicators: dict,
    ) -> None:
        ts = time.time()
        self._insert("signals", {
            "timestamp": ts,
            "timestamp_kst": _ts_to_kst_iso(ts),
            "symbol": symbol,
            "strategy_name": strategy_name,
            "action": action,
            "price": price,
            "indicator_json": indicators,
        })

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
        self._insert("account_state", {
            "timestamp": ts,
            "timestamp_kst": _ts_to_kst_iso(ts),
            "balance_usdt": state.balance_usdt,
            "positions_json": positions_dict,
            "realized_pnl": state.realized_pnl,
            "mode": mode,
        })

    def load_account_state(self) -> AccountState | None:
        rows = self._select("account_state", {
            "select": "*", "order": "id.desc", "limit": "1",
        })
        if not rows:
            return None
        row = rows[0]

        raw_pos = row.get("positions_json") or {}
        positions: dict[str, Position] = {}
        for sym, d in raw_pos.items():
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
    def get_trades(self, symbol: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        params = {"select": "*", "order": "id.desc", "limit": str(limit), "offset": str(offset)}
        if symbol:
            params["symbol"] = f"eq.{symbol}"
        return self._select("trades", params)

    def get_equity_curve(self, since_ts: float | None = None, limit: int = 2000) -> list[dict]:
        params = {"select": "*", "order": "id.asc", "limit": str(limit)}
        if since_ts is not None:
            params["timestamp"] = f"gte.{since_ts}"
        return self._select("equity_curve", params)

    def get_performance_report(self) -> dict:
        """trades + equity_curve 기반 성과 지표. Storage.get_performance_report 와 동일 계산."""
        trades_rows = self._select("trades", {"select": "*", "order": "id.asc", "limit": "100000"})
        equity_rows = self._select("equity_curve", {"select": "total_equity,timestamp,unrealized_pnl", "order": "id.asc", "limit": "100000"})

        trade_count = len(trades_rows)
        num_liquidations = sum(1 for r in trades_rows if r.get("is_liquidation"))
        realized_pnl_usdt = sum(r.get("realized_pnl", 0.0) for r in trades_rows)

        close_trades = [r for r in trades_rows if r.get("action") == "CLOSE"]
        wins = [r for r in close_trades if r.get("realized_pnl", 0.0) > 0]
        losses = [r for r in close_trades if r.get("realized_pnl", 0.0) <= 0]
        win_rate_pct = (len(wins) / len(close_trades) * 100) if close_trades else 0.0
        avg_win_usdt = (sum(r["realized_pnl"] for r in wins) / len(wins)) if wins else 0.0
        avg_loss_usdt = (sum(r["realized_pnl"] for r in losses) / len(losses)) if losses else 0.0
        gross_win = sum(r["realized_pnl"] for r in wins)
        gross_loss = abs(sum(r["realized_pnl"] for r in losses))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 0.0

        equities = [r["total_equity"] for r in equity_rows]
        total_return_pct = mdd_pct = sharpe_ratio = daily_return_pct = unrealized_pnl_usdt = 0.0

        if equities:
            start_eq, end_eq = equities[0], equities[-1]
            total_return_pct = ((end_eq - start_eq) / start_eq * 100) if start_eq > 0 else 0.0

            cum_max = equities[0]
            max_dd = 0.0
            for eq in equities:
                cum_max = max(cum_max, eq)
                dd = (eq - cum_max) / cum_max * 100 if cum_max > 0 else 0.0
                max_dd = min(max_dd, dd)
            mdd_pct = max_dd

            if len(equities) >= 2:
                rets = [
                    (equities[i] - equities[i - 1]) / equities[i - 1]
                    for i in range(1, len(equities)) if equities[i - 1] > 0
                ]
                if len(rets) >= 2:
                    mean_r = sum(rets) / len(rets)
                    var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
                    std_r = math.sqrt(var_r) if var_r > 0 else 0.0
                    sharpe_ratio = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0

            if equity_rows:
                days = max((equity_rows[-1]["timestamp"] - equity_rows[0]["timestamp"]) / 86400, 1.0)
                daily_return_pct = total_return_pct / days
                unrealized_pnl_usdt = equity_rows[-1].get("unrealized_pnl", 0.0)

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
        try:
            self._session.close()
        except Exception:
            logger.exception("close() 실패")
