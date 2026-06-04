"""전략 패키지.

이 패키지를 import 하면 모든 구현 전략이 StrategyRegistry 에 자동 등록된다.
(orchestrator / backtest / optimize / run 어디서든 StrategyRegistry.create 가 동작하도록 보장)
"""

from . import base  # noqa: F401
from . import volatility_breakout  # noqa: F401
from . import ma_crossover  # noqa: F401
from . import ma_trend  # noqa: F401
from . import rsi_mean_reversion  # noqa: F401
