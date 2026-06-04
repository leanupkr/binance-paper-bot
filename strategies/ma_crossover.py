"""MA 크로스오버 전략 — 골든크로스(short SMA > long SMA) LONG, 데드크로스 SHORT."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType, StrategyRegistry


@StrategyRegistry.register
class MACrossoverStrategy(BaseStrategy):
    name = "ma_crossover"
    default_params = {"short": 5, "long": 20}

    def __init__(self, params: dict) -> None:
        super().__init__(params)
        short_w: int = int(self.params["short"])
        long_w: int = int(self.params["long"])
        if short_w >= long_w:
            raise ValueError(
                f"short({short_w}) >= long({long_w}): short 기간은 long 보다 작아야 한다."
            )

    def on_data(self, symbol: str, candles: pd.DataFrame, price: float) -> Signal:
        long_w: int = int(self.params["long"])
        short_w: int = int(self.params["short"])

        # 최소 데이터: long+1 봉 필요(직전/현재 비교)
        if len(candles) < long_w + 1:
            return Signal(type=SignalType.HOLD, symbol=symbol, price=price)

        close: pd.Series = candles["close"].astype(float)
        short_ma: pd.Series = close.rolling(short_w).mean()
        long_ma: pd.Series = close.rolling(long_w).mean()

        prev_short = short_ma.iloc[-2]
        prev_long = long_ma.iloc[-2]
        curr_short = short_ma.iloc[-1]
        curr_long = long_ma.iloc[-1]

        indicators = {
            "short_ma": round(float(curr_short), 6),
            "long_ma": round(float(curr_long), 6),
        }

        # 골든크로스: 직전 short < long, 현재 short > long
        if prev_short < prev_long and curr_short > curr_long:
            return Signal(
                type=SignalType.LONG,
                symbol=symbol,
                price=price,
                reason="골든크로스",
                indicators=indicators,
            )
        # 데드크로스: 직전 short > long, 현재 short < long
        if prev_short > prev_long and curr_short < curr_long:
            return Signal(
                type=SignalType.SHORT,
                symbol=symbol,
                price=price,
                reason="데드크로스",
                indicators=indicators,
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            indicators=indicators,
        )
