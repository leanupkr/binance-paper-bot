"""변동성 돌파 전략 — 전일 고저 범위를 k 배 적용해 상/하방 돌파 시 LONG/SHORT."""

from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType, StrategyRegistry


@StrategyRegistry.register
class VolatilityBreakoutStrategy(BaseStrategy):
    name = "volatility_breakout"
    default_params = {"k": 0.5}

    def on_data(self, symbol: str, candles: pd.DataFrame, price: float) -> Signal:
        if len(candles) < 2:
            return Signal(type=SignalType.HOLD, symbol=symbol, price=price)

        prev = candles.iloc[-2]
        curr_open = candles.iloc[-1]["open"]
        k: float = self.params["k"]

        range_: float = float(prev["high"]) - float(prev["low"])
        up_target: float = float(curr_open) + range_ * k
        down_target: float = float(curr_open) - range_ * k

        if price >= up_target:
            return Signal(
                type=SignalType.LONG,
                symbol=symbol,
                price=price,
                reason="상방 돌파",
                indicators={"up_target": up_target, "down_target": down_target},
            )
        if price <= down_target:
            return Signal(
                type=SignalType.SHORT,
                symbol=symbol,
                price=price,
                reason="하방 돌파",
                indicators={"up_target": up_target, "down_target": down_target},
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            indicators={"up_target": up_target, "down_target": down_target},
        )
