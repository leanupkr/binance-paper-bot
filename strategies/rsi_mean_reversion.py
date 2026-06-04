"""RSI 평균회귀 전략 — Wilder RSI 과매도 LONG, 과매수 SHORT."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType, StrategyRegistry


@StrategyRegistry.register
class RSIMeanReversionStrategy(BaseStrategy):
    name = "rsi_mean_reversion"
    default_params = {"period": 14, "oversold": 30, "overbought": 70}

    @staticmethod
    def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
        """Wilder 스무딩 방식 RSI."""
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)

        # 첫 평균: 단순 평균
        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        # avg_loss == 0 (하락 전무) → RSI=100. 단 avg_gain==avg_loss==0 (완전 평평) → 50.
        rsi = rsi.where(avg_loss != 0, 100.0)
        rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
        return rsi

    def on_data(self, symbol: str, candles: pd.DataFrame, price: float) -> Signal:
        period: int = int(self.params["period"])
        oversold: float = float(self.params["oversold"])
        overbought: float = float(self.params["overbought"])

        if len(candles) < period + 1:
            return Signal(type=SignalType.HOLD, symbol=symbol, price=price)

        close = candles["close"].astype(float)
        rsi_series = self._compute_rsi(close, period)
        current_rsi = float(rsi_series.iloc[-1])

        indicators = {"rsi": round(current_rsi, 4)}

        if current_rsi <= oversold:
            return Signal(
                type=SignalType.LONG,
                symbol=symbol,
                price=price,
                reason=f"RSI 과매도({current_rsi:.1f}<={oversold})",
                indicators=indicators,
            )
        if current_rsi >= overbought:
            return Signal(
                type=SignalType.SHORT,
                symbol=symbol,
                price=price,
                reason=f"RSI 과매수({current_rsi:.1f}>={overbought})",
                indicators=indicators,
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            indicators=indicators,
        )
