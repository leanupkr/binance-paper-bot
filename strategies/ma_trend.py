"""MA 추세추종 (always-in) 전략.

크로스오버와 달리 '교차 순간'만 보지 않고, 매 평가마다 현재 추세 방향으로
**항상 롱 또는 숏 포지션을 유지**한다.
  - 단기 SMA >= 장기 SMA → LONG
  - 단기 SMA <  장기 SMA → SHORT
HOLD 는 데이터 부족 시에만. 따라서 봇은 늘 한쪽 방향에 포지션을 갖고,
추세가 바뀌면(단기/장기 MA 역전) 청산 후 반대로 전환한다.

주의: always-in + 짧은 타임프레임은 전환(=청산+진입)이 잦아 수수료가 누적되고
횡보장에서 휩쏘로 손실이 날 수 있다. 거래 빈도와 수수료를 함께 본다.
"""
from __future__ import annotations

import pandas as pd

from strategies.base import BaseStrategy, Signal, SignalType, StrategyRegistry


@StrategyRegistry.register
class MATrendStrategy(BaseStrategy):
    name = "ma_trend"
    default_params = {"short": 5, "long": 15}

    def __init__(self, params: dict) -> None:
        super().__init__(params)
        short_w = int(self.params["short"])
        long_w = int(self.params["long"])
        if short_w >= long_w:
            raise ValueError(
                f"short({short_w}) >= long({long_w}): short 기간은 long 보다 작아야 한다."
            )

    def on_data(self, symbol: str, candles: pd.DataFrame, price: float) -> Signal:
        long_w = int(self.params["long"])
        short_w = int(self.params["short"])

        if len(candles) < long_w + 1:
            return Signal(type=SignalType.HOLD, symbol=symbol, price=price)

        close = candles["close"].astype(float)
        short_ma = float(close.rolling(short_w).mean().iloc[-1])
        long_ma = float(close.rolling(long_w).mean().iloc[-1])
        indicators = {"short_ma": round(short_ma, 6), "long_ma": round(long_ma, 6)}

        if short_ma >= long_ma:
            return Signal(
                type=SignalType.LONG, symbol=symbol, price=price,
                reason=f"추세 롱(단기 {short_ma:.4f} >= 장기 {long_ma:.4f})",
                indicators=indicators,
            )
        return Signal(
            type=SignalType.SHORT, symbol=symbol, price=price,
            reason=f"추세 숏(단기 {short_ma:.4f} < 장기 {long_ma:.4f})",
            indicators=indicators,
        )
