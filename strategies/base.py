"""
바이낸스 USDT-M 선물 전략 인터페이스 + 레지스트리.

캔들 DataFrame 규격:
    columns = ["open", "high", "low", "close", "volume"]
    index   = DatetimeIndex(오름차순), iloc[-1] 이 최신 봉.

원칙: 전략은 방향성 신호만 낸다(LONG/SHORT/CLOSE/HOLD).
실제 보유 여부·중복·반대전환 처리는 호출자(orchestrator/backtest)가 담당한다.
"""

from __future__ import annotations

import pandas as pd
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar


class SignalType(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


@dataclass
class Signal:
    type: SignalType
    symbol: str
    price: float
    size_pct: float = 0.0          # 0~1, 가용잔고 대비 진입 증거금 비중 제안(0이면 리스크모듈 기본값)
    leverage: int | None = None    # 전략이 제안하는 레버리지(없으면 config 기본)
    reason: str = ""
    indicators: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """전략 베이스 클래스. 서브클래스는 name, default_params 클래스 속성과 on_data 를 반드시 정의."""

    name: ClassVar[str]
    default_params: ClassVar[dict]

    def __init__(self, params: dict) -> None:
        self.params: dict = {**self.default_params, **(params or {})}

    @abstractmethod
    def on_data(self, symbol: str, candles: pd.DataFrame, price: float) -> Signal:
        """최신 캔들과 현재가를 받아 Signal 을 반환한다."""


class StrategyRegistry:
    """전략 클래스 레지스트리. register 데코레이터로 등록, create 로 인스턴스화."""

    _registry: ClassVar[dict[str, type[BaseStrategy]]] = {}

    @classmethod
    def register(cls, strategy_cls: type[BaseStrategy]) -> type[BaseStrategy]:
        """데코레이터: strategy_cls.name 으로 등록 후 strategy_cls 반환."""
        cls._registry[strategy_cls.name] = strategy_cls
        return strategy_cls

    @classmethod
    def create(cls, name: str, params: dict) -> BaseStrategy:
        """등록된 전략 이름으로 인스턴스를 생성한다. 미등록 시 ValueError."""
        if name not in cls._registry:
            available = cls.list_strategies()
            raise ValueError(
                f"전략 '{name}' 미등록. 사용 가능: {available}"
            )
        return cls._registry[name](params)

    @classmethod
    def list_strategies(cls) -> list[str]:
        """등록된 전략 이름 목록을 반환한다."""
        return list(cls._registry.keys())
