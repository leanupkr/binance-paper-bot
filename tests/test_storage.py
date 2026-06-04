"""
Storage 단위 테스트.
tmp_path SQLite DB 로 initialize → record → get 조회, save/load_account_state 왕복.
"""

import os
import time
import pytest

from core.paper_wallet import (
    AccountState,
    CloseResult,
    ExecutionResult,
    OpenResult,
    Position,
    PositionSide,
)
from core.storage import Storage


# ── 픽스처 ────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path) -> Storage:
    s = Storage(str(tmp_path / "test.db"))
    s.initialize()
    return s


def _open_result(symbol: str = "BTCUSDT", side: PositionSide = PositionSide.LONG) -> OpenResult:
    return OpenResult(
        symbol=symbol,
        side=side,
        filled_price=30000.0,
        quantity=0.01,
        notional=300.0,
        fee_usdt=0.15,
        margin_usdt=100.0,
        liquidation_price=27000.0,
        new_entry_price=30000.0,
        timestamp=time.time(),
    )


def _close_result(
    symbol: str = "BTCUSDT",
    side: PositionSide = PositionSide.LONG,
    is_liquidation: bool = False,
) -> CloseResult:
    return CloseResult(
        symbol=symbol,
        side=side,
        filled_price=31000.0,
        quantity=0.01,
        gross_pnl=10.0,
        fee_usdt=0.155,
        realized_pnl=9.845,
        returned_margin=100.0,
        timestamp=time.time(),
        is_liquidation=is_liquidation,
    )


# ── 테스트 ────────────────────────────────────────────────────

def test_initialize_creates_tables(db: Storage) -> None:
    """initialize 후 4개 테이블이 존재해야 한다."""
    tables = {
        row[0]
        for row in db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"trades", "equity_curve", "signals", "account_state"} <= tables


def test_record_trade_open(db: Storage) -> None:
    result = ExecutionResult(success=True, action="OPEN", detail=_open_result())
    db.record_trade(result, "BTCUSDT", "STRATEGY", "ma_crossover", 30000.0, 1000.0)

    rows = db.get_trades()
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "BTCUSDT"
    assert row["action"] == "OPEN"
    assert row["side"] == "LONG"
    assert row["trigger"] == "STRATEGY"
    assert row["strategy_name"] == "ma_crossover"
    assert abs(row["filled_price"] - 30000.0) < 1e-6
    assert row["is_liquidation"] == 0


def test_record_trade_close(db: Storage) -> None:
    result = ExecutionResult(success=True, action="CLOSE", detail=_close_result())
    db.record_trade(result, "BTCUSDT", "STOP_LOSS", "ma_crossover", 31000.0, 1009.845)

    rows = db.get_trades()
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "CLOSE"
    assert abs(row["realized_pnl"] - 9.845) < 1e-6
    assert row["is_liquidation"] == 0


def test_record_trade_liquidation(db: Storage) -> None:
    result = ExecutionResult(
        success=True, action="CLOSE", detail=_close_result(is_liquidation=True)
    )
    db.record_trade(result, "BTCUSDT", "LIQUIDATION", "test", 27000.0, 900.0)

    rows = db.get_trades()
    assert rows[0]["is_liquidation"] == 1


def test_record_trade_failed_result_ignored(db: Storage) -> None:
    """success=False 인 ExecutionResult 는 무시."""
    result = ExecutionResult(success=False, action="OPEN", rejection_reason="INSUFFICIENT_BALANCE")
    db.record_trade(result, "BTCUSDT", "STRATEGY", "test", 30000.0, 1000.0)
    assert db.get_trades() == []


def test_get_trades_filter_by_symbol(db: Storage) -> None:
    for sym in ("BTCUSDT", "ETHUSDT", "BTCUSDT"):
        r = ExecutionResult(success=True, action="OPEN", detail=_open_result(symbol=sym))
        db.record_trade(r, sym, "STRATEGY", "test", 100.0, 1000.0)

    btc = db.get_trades(symbol="BTCUSDT")
    eth = db.get_trades(symbol="ETHUSDT")
    assert len(btc) == 2
    assert len(eth) == 1


def test_get_trades_limit_offset(db: Storage) -> None:
    for _ in range(5):
        r = ExecutionResult(success=True, action="OPEN", detail=_open_result())
        db.record_trade(r, "BTCUSDT", "STRATEGY", "test", 30000.0, 1000.0)

    assert len(db.get_trades(limit=3)) == 3
    assert len(db.get_trades(limit=3, offset=3)) == 2


def test_record_and_get_equity_curve(db: Storage) -> None:
    db.record_equity(1000.0, 900.0, 100.0, 50.0)
    db.record_equity(1050.0, 950.0, 100.0, 60.0)

    rows = db.get_equity_curve()
    assert len(rows) == 2
    assert abs(rows[0]["total_equity"] - 1000.0) < 1e-6
    assert abs(rows[1]["total_equity"] - 1050.0) < 1e-6


def test_get_equity_curve_since_ts(db: Storage) -> None:
    db.record_equity(900.0, 900.0, 0.0, 0.0)
    cutoff = time.time()
    db.record_equity(1000.0, 1000.0, 0.0, 0.0)

    rows = db.get_equity_curve(since_ts=cutoff)
    assert len(rows) == 1
    assert abs(rows[0]["total_equity"] - 1000.0) < 1e-6


def test_record_signal(db: Storage) -> None:
    db.record_signal("BTCUSDT", "rsi", "LONG", 30000.0, {"rsi": 28.5})
    row = db._conn.execute("SELECT * FROM signals").fetchone()
    assert row["symbol"] == "BTCUSDT"
    assert row["action"] == "LONG"
    assert "rsi" in row["indicator_json"]


# ── save/load_account_state 왕복 ──────────────────────────────

def test_save_load_account_state_no_positions(db: Storage) -> None:
    state = AccountState(balance_usdt=500.0, realized_pnl=10.0)
    db.save_account_state(state, mode="paper")

    loaded = db.load_account_state()
    assert loaded is not None
    assert abs(loaded.balance_usdt - 500.0) < 1e-6
    assert abs(loaded.realized_pnl - 10.0) < 1e-6
    assert loaded.positions == {}


def test_save_load_account_state_long_short(db: Storage) -> None:
    """롱+숏 포지션이 올바르게 복원되어야 한다."""
    positions = {
        "BTCUSDT": Position(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.05,
            entry_price=30000.0,
            leverage=3,
            margin_usdt=500.0,
            liquidation_price=20500.0,
        ),
        "ETHUSDT": Position(
            symbol="ETHUSDT",
            side=PositionSide.SHORT,
            quantity=1.0,
            entry_price=2000.0,
            leverage=5,
            margin_usdt=400.0,
            liquidation_price=2360.0,
        ),
    }
    state = AccountState(balance_usdt=100.0, positions=positions, realized_pnl=-20.0)
    db.save_account_state(state, mode="paper")

    loaded = db.load_account_state()
    assert loaded is not None
    assert abs(loaded.balance_usdt - 100.0) < 1e-6
    assert abs(loaded.realized_pnl - (-20.0)) < 1e-6

    btc = loaded.positions["BTCUSDT"]
    assert btc.side == PositionSide.LONG
    assert abs(btc.quantity - 0.05) < 1e-9
    assert btc.leverage == 3

    eth = loaded.positions["ETHUSDT"]
    assert eth.side == PositionSide.SHORT
    assert abs(eth.entry_price - 2000.0) < 1e-6
    assert eth.leverage == 5


def test_load_account_state_empty(db: Storage) -> None:
    """저장된 상태 없으면 None 반환."""
    assert db.load_account_state() is None


def test_save_load_account_state_latest_wins(db: Storage) -> None:
    """여러 번 저장 시 마지막 상태가 복원된다."""
    db.save_account_state(AccountState(balance_usdt=100.0))
    db.save_account_state(AccountState(balance_usdt=200.0))
    db.save_account_state(AccountState(balance_usdt=300.0))

    loaded = db.load_account_state()
    assert loaded is not None
    assert abs(loaded.balance_usdt - 300.0) < 1e-6


# ── get_performance_report ────────────────────────────────────

def test_performance_report_empty_db(db: Storage) -> None:
    """빈 DB 에서 예외 없이 기본값 반환."""
    report = db.get_performance_report()
    assert report["trade_count"] == 0
    assert report["win_rate_pct"] == 0.0
    assert report["profit_factor"] == 0.0
    assert report["num_liquidations"] == 0
    assert report["total_return_pct"] == 0.0


def test_performance_report_with_data(db: Storage) -> None:
    """거래 + equity curve 있을 때 기본 지표 검증."""
    # equity curve: 1000 → 1100
    db.record_equity(1000.0, 900.0, 100.0, 0.0)
    db.record_equity(1100.0, 1000.0, 100.0, 50.0)

    # 2승 1패
    for pnl, liq in [(20.0, False), (15.0, False), (-10.0, False)]:
        cr = CloseResult(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            filled_price=31000.0,
            quantity=0.01,
            gross_pnl=pnl,
            fee_usdt=0.0,
            realized_pnl=pnl,
            returned_margin=100.0,
            timestamp=time.time(),
            is_liquidation=liq,
        )
        r = ExecutionResult(success=True, action="CLOSE", detail=cr)
        db.record_trade(r, "BTCUSDT", "STRATEGY", "test", 31000.0, 1000.0)

    # 청산 1건
    liq_cr = CloseResult(
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        filled_price=27000.0,
        quantity=0.01,
        gross_pnl=-300.0,
        fee_usdt=0.0,
        realized_pnl=-300.0,
        returned_margin=0.0,
        timestamp=time.time(),
        is_liquidation=True,
    )
    r_liq = ExecutionResult(success=True, action="CLOSE", detail=liq_cr)
    db.record_trade(r_liq, "BTCUSDT", "LIQUIDATION", "test", 27000.0, 700.0)

    report = db.get_performance_report()
    assert report["trade_count"] == 4
    assert report["num_liquidations"] == 1
    assert abs(report["win_rate_pct"] - 50.0) < 1e-6  # 2승 / (3+1 close 중 2승)
    assert report["profit_factor"] > 0
    assert report["total_return_pct"] > 0
    assert report["unrealized_pnl_usdt"] == pytest.approx(50.0)
