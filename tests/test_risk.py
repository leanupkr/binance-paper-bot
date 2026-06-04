"""
tests/test_risk.py — RiskManager 단위 테스트.

테스트 항목:
  - 롱/숏 손절 경계 (경계값 포함/미달)
  - 롱/숏 익절 경계
  - tp=0 비활성
  - allow_short=False 시 SHORT → HOLD
  - allow_long=False 시 LONG → HOLD
  - 일일한도 도달 시 LONG → HOLD
  - CLOSE/HOLD 는 항상 통과
  - resolve_leverage 캡(15→10, None→default)
"""
from __future__ import annotations

import pytest

from core.config import RiskConfig
from core.paper_wallet import PaperWallet, PositionSide
from core.risk import RiskManager
from strategies.base import Signal, SignalType


# ── 픽스처 ──────────────────────────────────────────────────────

def _make_config(
    position_size_pct: float = 20.0,
    stop_loss_pct: float = 3.0,
    take_profit_pct: float = 5.0,
    max_daily_loss_pct: float = 5.0,
    max_position_pct_per_symbol: float = 40.0,
    allow_long: bool = True,
    allow_short: bool = True,
) -> RiskConfig:
    return RiskConfig(
        position_size_pct=position_size_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_position_pct_per_symbol=max_position_pct_per_symbol,
        allow_long=allow_long,
        allow_short=allow_short,
    )


def _make_rm(config: RiskConfig, initial_equity: float = 1000.0) -> RiskManager:
    return RiskManager(
        config=config,
        initial_equity=initial_equity,
        leverage_default=3,
        leverage_max=10,
    )


def _make_wallet(balance: float = 1000.0) -> PaperWallet:
    return PaperWallet(initial_balance_usdt=balance)


# ── resolve_leverage ────────────────────────────────────────────

class TestResolveLeverage:
    def test_signal_within_max(self) -> None:
        rm = _make_rm(_make_config())
        assert rm.resolve_leverage(5) == 5

    def test_signal_exceeds_max_capped(self) -> None:
        """15 → 10 (max=10)"""
        rm = _make_rm(_make_config())
        assert rm.resolve_leverage(15) == 10

    def test_none_uses_default(self) -> None:
        """None → leverage_default=3"""
        rm = _make_rm(_make_config())
        assert rm.resolve_leverage(None) == 3

    def test_minimum_one(self) -> None:
        """0 또는 음수여도 최소 1"""
        rm = _make_rm(_make_config())
        assert rm.resolve_leverage(0) == 1


# ── check_stop_loss ─────────────────────────────────────────────

class TestStopLoss:
    def test_long_sl_triggered_at_boundary(self) -> None:
        """롱 손절: (price - entry)/entry == -sl_ratio → True"""
        rm = _make_rm(_make_config(stop_loss_pct=3.0))
        entry = 1000.0
        price = entry * (1.0 - 0.03)   # 정확히 경계
        assert rm.check_stop_loss(PositionSide.LONG, entry, price) is True

    def test_long_sl_not_triggered_above_boundary(self) -> None:
        """롱 손절: 경계보다 1원 위 → False"""
        rm = _make_rm(_make_config(stop_loss_pct=3.0))
        entry = 1000.0
        price = entry * (1.0 - 0.03) + 0.01
        assert rm.check_stop_loss(PositionSide.LONG, entry, price) is False

    def test_long_sl_triggered_below_boundary(self) -> None:
        """롱 손절: 경계보다 더 하락 → True"""
        rm = _make_rm(_make_config(stop_loss_pct=3.0))
        entry = 1000.0
        price = entry * (1.0 - 0.04)
        assert rm.check_stop_loss(PositionSide.LONG, entry, price) is True

    def test_short_sl_triggered_at_boundary(self) -> None:
        """숏 손절: (entry - price)/entry == -sl_ratio → True"""
        rm = _make_rm(_make_config(stop_loss_pct=3.0))
        entry = 1000.0
        price = entry * (1.0 + 0.03)   # 숏은 가격 상승이 손실
        assert rm.check_stop_loss(PositionSide.SHORT, entry, price) is True

    def test_short_sl_not_triggered(self) -> None:
        """숏 손절: 경계보다 1원 아래 상승 → False"""
        rm = _make_rm(_make_config(stop_loss_pct=3.0))
        entry = 1000.0
        price = entry * (1.0 + 0.03) - 0.01
        assert rm.check_stop_loss(PositionSide.SHORT, entry, price) is False

    def test_short_sl_triggered_above_boundary(self) -> None:
        """숏 손절: 경계보다 더 상승 → True"""
        rm = _make_rm(_make_config(stop_loss_pct=3.0))
        entry = 1000.0
        price = entry * (1.0 + 0.04)
        assert rm.check_stop_loss(PositionSide.SHORT, entry, price) is True


# ── check_take_profit ────────────────────────────────────────────

class TestTakeProfit:
    def test_long_tp_triggered_at_boundary(self) -> None:
        """롱 익절: (price - entry)/entry == tp_ratio → True"""
        rm = _make_rm(_make_config(take_profit_pct=5.0))
        entry = 1000.0
        price = entry * (1.0 + 0.05)
        assert rm.check_take_profit(PositionSide.LONG, entry, price) is True

    def test_long_tp_not_triggered_below_boundary(self) -> None:
        rm = _make_rm(_make_config(take_profit_pct=5.0))
        entry = 1000.0
        price = entry * (1.0 + 0.05) - 0.01
        assert rm.check_take_profit(PositionSide.LONG, entry, price) is False

    def test_short_tp_triggered_at_boundary(self) -> None:
        """숏 익절: (entry - price)/entry == tp_ratio → True"""
        rm = _make_rm(_make_config(take_profit_pct=5.0))
        entry = 1000.0
        price = entry * (1.0 - 0.05)
        assert rm.check_take_profit(PositionSide.SHORT, entry, price) is True

    def test_short_tp_not_triggered(self) -> None:
        rm = _make_rm(_make_config(take_profit_pct=5.0))
        entry = 1000.0
        price = entry * (1.0 - 0.05) + 0.01
        assert rm.check_take_profit(PositionSide.SHORT, entry, price) is False

    def test_tp_zero_disabled_long(self) -> None:
        """tp=0 → 항상 False (비활성)"""
        rm = _make_rm(_make_config(take_profit_pct=0.0))
        entry = 1000.0
        price = entry * 2.0  # 100% 수익에도 False
        assert rm.check_take_profit(PositionSide.LONG, entry, price) is False

    def test_tp_zero_disabled_short(self) -> None:
        rm = _make_rm(_make_config(take_profit_pct=0.0))
        entry = 1000.0
        price = 1.0
        assert rm.check_take_profit(PositionSide.SHORT, entry, price) is False


# ── validate_signal ──────────────────────────────────────────────

class TestValidateSignal:
    def _signal(self, stype: SignalType, symbol: str = "BTCUSDT", price: float = 50000.0) -> Signal:
        return Signal(type=stype, symbol=symbol, price=price)

    def test_close_always_passes(self) -> None:
        rm = _make_rm(_make_config(allow_long=False, allow_short=False))
        wallet = _make_wallet()
        sig = self._signal(SignalType.CLOSE)
        result = rm.validate_signal(sig, "BTCUSDT", wallet, {"BTCUSDT": 50000.0}, 1000.0)
        assert result.type == SignalType.CLOSE

    def test_hold_always_passes(self) -> None:
        rm = _make_rm(_make_config(allow_long=False, allow_short=False))
        wallet = _make_wallet()
        sig = self._signal(SignalType.HOLD)
        result = rm.validate_signal(sig, "BTCUSDT", wallet, {"BTCUSDT": 50000.0}, 1000.0)
        assert result.type == SignalType.HOLD

    def test_short_blocked_when_allow_short_false(self) -> None:
        """allow_short=False → SHORT 신호 HOLD 강등"""
        rm = _make_rm(_make_config(allow_short=False))
        wallet = _make_wallet()
        sig = self._signal(SignalType.SHORT)
        result = rm.validate_signal(sig, "BTCUSDT", wallet, {"BTCUSDT": 50000.0}, 1000.0)
        assert result.type == SignalType.HOLD

    def test_long_blocked_when_allow_long_false(self) -> None:
        """allow_long=False → LONG 신호 HOLD 강등"""
        rm = _make_rm(_make_config(allow_long=False))
        wallet = _make_wallet()
        sig = self._signal(SignalType.LONG)
        result = rm.validate_signal(sig, "BTCUSDT", wallet, {"BTCUSDT": 50000.0}, 1000.0)
        assert result.type == SignalType.HOLD

    def test_long_blocked_on_daily_loss_limit(self) -> None:
        """
        일일 손실 한도 도달 시 LONG → HOLD.
        초기 자산 1000, 한도 5% → 950 이하면 차단.
        """
        rm = _make_rm(_make_config(max_daily_loss_pct=5.0), initial_equity=1000.0)
        # 지갑 잔고를 940으로 낮춰 현재 equity=940 (한도 초과)
        wallet = _make_wallet(balance=940.0)
        sig = self._signal(SignalType.LONG)
        result = rm.validate_signal(sig, "BTCUSDT", wallet, {"BTCUSDT": 50000.0}, 1000.0)
        assert result.type == SignalType.HOLD

    def test_short_blocked_on_daily_loss_limit(self) -> None:
        """일일 손실 한도 도달 시 SHORT → HOLD."""
        rm = _make_rm(_make_config(max_daily_loss_pct=5.0), initial_equity=1000.0)
        wallet = _make_wallet(balance=940.0)
        sig = self._signal(SignalType.SHORT)
        result = rm.validate_signal(sig, "BTCUSDT", wallet, {"BTCUSDT": 50000.0}, 1000.0)
        assert result.type == SignalType.HOLD

    def test_long_passes_within_daily_limit(self) -> None:
        """한도 미달 → LONG 통과"""
        rm = _make_rm(_make_config(max_daily_loss_pct=5.0), initial_equity=1000.0)
        wallet = _make_wallet(balance=960.0)  # 4% 손실, 한도 5% → 통과
        sig = self._signal(SignalType.LONG)
        result = rm.validate_signal(sig, "BTCUSDT", wallet, {"BTCUSDT": 50000.0}, 1000.0)
        assert result.type == SignalType.LONG


# ── check_daily_loss_limit ───────────────────────────────────────

class TestDailyLossLimit:
    def test_triggered_at_exact_boundary(self) -> None:
        rm = _make_rm(_make_config(max_daily_loss_pct=5.0))
        # (1000 - 950) / 1000 = 0.05 == 0.05 → True
        assert rm.check_daily_loss_limit(1000.0, 950.0) is True

    def test_not_triggered_above_boundary(self) -> None:
        # (1000 - 951) / 1000 = 0.049 < 0.05 → False
        rm = _make_rm(_make_config(max_daily_loss_pct=5.0))
        assert rm.check_daily_loss_limit(1000.0, 951.0) is False

    def test_triggered_below_boundary(self) -> None:
        rm = _make_rm(_make_config(max_daily_loss_pct=5.0))
        assert rm.check_daily_loss_limit(1000.0, 900.0) is True

    def test_zero_start_equity_safe(self) -> None:
        """start_equity=0 → ZeroDivision 방지, False 반환"""
        rm = _make_rm(_make_config())
        assert rm.check_daily_loss_limit(0.0, 0.0) is False


# ── reset_daily_tracking ─────────────────────────────────────────

class TestResetDailyTracking:
    def test_reset_updates_start(self) -> None:
        """reset 후 새 equity 기준으로 한도 재계산"""
        rm = _make_rm(_make_config(max_daily_loss_pct=5.0), initial_equity=1000.0)
        # 초기 기준 1000 → equity 950 → 한도 도달
        assert rm.check_daily_loss_limit(1000.0, 950.0) is True

        # reset: 기준을 950으로 갱신
        rm.reset_daily_tracking(950.0)
        # 새 기준 950 → equity 940 → 손실 10/950 ≈ 1.05% < 5% → False
        assert rm.check_daily_loss_limit(950.0, 940.0) is False
