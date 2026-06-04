"""전략 3종 유닛 테스트."""

from __future__ import annotations

import pytest
import pandas as pd
import numpy as np

# 레지스트리에 등록시키기 위해 import
import strategies.volatility_breakout  # noqa: F401
import strategies.ma_crossover         # noqa: F401
import strategies.rsi_mean_reversion   # noqa: F401

from strategies.base import SignalType, StrategyRegistry


# ── 헬퍼 ──────────────────────────────────────────────────────────

def _make_candles(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    n = len(closes)
    if volumes is None:
        volumes = [1000.0] * n
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def _uniform_candles(price: float, n: int) -> pd.DataFrame:
    """고저종시 모두 동일한 단조 캔들."""
    return _make_candles(
        [price] * n,
        [price] * n,
        [price] * n,
        [price] * n,
    )


# ── 변동성 돌파 ────────────────────────────────────────────────────

class TestVolatilityBreakout:
    SYMBOL = "BTCUSDT"

    def _strategy(self) -> object:
        return StrategyRegistry.create("volatility_breakout", {})

    def _candles(self, prev_high: float, prev_low: float, curr_open: float) -> pd.DataFrame:
        """2봉짜리 최소 캔들: iloc[-2]=전봉(고저), iloc[-1]=현봉(시가)."""
        return _make_candles(
            opens=[curr_open - 1, curr_open],
            highs=[prev_high, curr_open],
            lows=[prev_low, curr_open],
            closes=[curr_open - 0.5, curr_open],
        )

    def test_long_when_price_equals_up_target(self) -> None:
        s = self._strategy()
        k = s.params["k"]
        prev_high, prev_low, curr_open = 110.0, 90.0, 100.0
        up_target = curr_open + (prev_high - prev_low) * k  # 100 + 20*0.5 = 110
        candles = self._candles(prev_high, prev_low, curr_open)
        sig = s.on_data(self.SYMBOL, candles, price=up_target)
        assert sig.type == SignalType.LONG
        assert sig.indicators["up_target"] == pytest.approx(up_target)

    def test_short_when_price_equals_down_target(self) -> None:
        s = self._strategy()
        k = s.params["k"]
        prev_high, prev_low, curr_open = 110.0, 90.0, 100.0
        down_target = curr_open - (prev_high - prev_low) * k  # 100 - 10 = 90
        candles = self._candles(prev_high, prev_low, curr_open)
        sig = s.on_data(self.SYMBOL, candles, price=down_target)
        assert sig.type == SignalType.SHORT
        assert sig.indicators["down_target"] == pytest.approx(down_target)

    def test_hold_when_price_between_targets(self) -> None:
        s = self._strategy()
        k = s.params["k"]
        prev_high, prev_low, curr_open = 110.0, 90.0, 100.0
        up_target = curr_open + (prev_high - prev_low) * k
        down_target = curr_open - (prev_high - prev_low) * k
        mid_price = (up_target + down_target) / 2
        candles = self._candles(prev_high, prev_low, curr_open)
        sig = s.on_data(self.SYMBOL, candles, price=mid_price)
        assert sig.type == SignalType.HOLD

    def test_hold_when_insufficient_data(self) -> None:
        s = self._strategy()
        candles = _make_candles([100.0], [110.0], [90.0], [100.0])  # 1봉
        sig = s.on_data(self.SYMBOL, candles, price=100.0)
        assert sig.type == SignalType.HOLD


# ── MA 크로스오버 ──────────────────────────────────────────────────

class TestMACrossover:
    SYMBOL = "ETHUSDT"

    def test_golden_cross_returns_long(self) -> None:
        """단조 상승 데이터 → 단기 SMA가 장기 SMA를 위로 돌파 → LONG."""
        s = StrategyRegistry.create("ma_crossover", {"short": 3, "long": 5})
        # V자(하락 후 반등): 마지막 봉에서 단기MA가 장기MA를 위로 클린 돌파
        # 직전봉 short<long(86.67<94), 현재봉 short>long(103.33>100)
        closes = [120.0, 110.0, 100.0, 90.0, 80.0, 90.0, 140.0]
        n = len(closes)
        candles = _make_candles(
            opens=closes,
            highs=[c + 1 for c in closes],
            lows=[c - 1 for c in closes],
            closes=closes,
        )
        sig = s.on_data(self.SYMBOL, candles, price=closes[-1])
        assert sig.type == SignalType.LONG

    def test_dead_cross_returns_short(self) -> None:
        """단조 하락 데이터 → 단기 SMA가 장기 SMA를 아래로 돌파 → SHORT."""
        s = StrategyRegistry.create("ma_crossover", {"short": 3, "long": 5})
        # 역V자(상승 후 하락): 마지막 봉에서 단기MA가 장기MA를 아래로 클린 돌파
        # 직전봉 short>long(113.33>106), 현재봉 short<long(96.67<100)
        closes = [80.0, 90.0, 100.0, 110.0, 120.0, 110.0, 60.0]
        candles = _make_candles(
            opens=closes,
            highs=[c + 1 for c in closes],
            lows=[c - 1 for c in closes],
            closes=closes,
        )
        sig = s.on_data(self.SYMBOL, candles, price=closes[-1])
        assert sig.type == SignalType.SHORT

    def test_short_ge_long_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            StrategyRegistry.create("ma_crossover", {"short": 20, "long": 5})

    def test_short_equal_long_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            StrategyRegistry.create("ma_crossover", {"short": 10, "long": 10})

    def test_hold_when_insufficient_data(self) -> None:
        s = StrategyRegistry.create("ma_crossover", {"short": 5, "long": 20})
        candles = _uniform_candles(100.0, 10)  # < long+1=21
        sig = s.on_data(self.SYMBOL, candles, price=100.0)
        assert sig.type == SignalType.HOLD


# ── RSI 평균회귀 ───────────────────────────────────────────────────

class TestRSIMeanReversion:
    SYMBOL = "SOLUSDT"

    def test_overbought_returns_short(self) -> None:
        """단조 상승 → RSI 높음 → SHORT."""
        s = StrategyRegistry.create("rsi_mean_reversion", {"period": 14, "oversold": 30, "overbought": 70})
        # 단조 상승: 매 봉 5% 상승
        closes = [100.0 * (1.05 ** i) for i in range(30)]
        candles = _make_candles(
            opens=closes,
            highs=[c * 1.001 for c in closes],
            lows=[c * 0.999 for c in closes],
            closes=closes,
        )
        sig = s.on_data(self.SYMBOL, candles, price=closes[-1])
        assert sig.type == SignalType.SHORT

    def test_oversold_returns_long(self) -> None:
        """단조 하락 → RSI 낮음 → LONG."""
        s = StrategyRegistry.create("rsi_mean_reversion", {"period": 14, "oversold": 30, "overbought": 70})
        closes = [100.0 * (0.95 ** i) for i in range(30)]
        candles = _make_candles(
            opens=closes,
            highs=[c * 1.001 for c in closes],
            lows=[c * 0.999 for c in closes],
            closes=closes,
        )
        sig = s.on_data(self.SYMBOL, candles, price=closes[-1])
        assert sig.type == SignalType.LONG

    def test_hold_when_insufficient_data(self) -> None:
        s = StrategyRegistry.create("rsi_mean_reversion", {"period": 14})
        candles = _uniform_candles(100.0, 10)  # < period+1=15
        sig = s.on_data(self.SYMBOL, candles, price=100.0)
        assert sig.type == SignalType.HOLD


# ── StrategyRegistry ──────────────────────────────────────────────

class TestStrategyRegistry:
    def test_unregistered_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            StrategyRegistry.create("no_such_strategy", {})

    def test_registered_strategies_listed(self) -> None:
        names = StrategyRegistry.list_strategies()
        assert "volatility_breakout" in names
        assert "ma_crossover" in names
        assert "rsi_mean_reversion" in names
