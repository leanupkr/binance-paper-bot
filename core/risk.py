"""
core/risk.py — 리스크 관리 모듈 (바이낸스 USDT-M 선물, 롱/숏).

RiskConfig의 % 값은 __init__에서 /100 비율로 변환해 보관한다.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.config import RiskConfig
from core.paper_wallet import PaperWallet, Position, PositionSide
from strategies.base import Signal, SignalType

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class RiskManager:
    """
    진입 증거금 계산, 레버리지 검증, 손절/익절/일일한도 판정,
    신호 강등(allow_long/short, 일일한도) 담당.
    """

    def __init__(
        self,
        config: RiskConfig,
        initial_equity: float,
        leverage_default: int,
        leverage_max: int,
    ) -> None:
        # % → 비율 변환 (내부에서만 비율 사용)
        self._size_ratio: float = config.position_size_pct / 100.0
        self._sl_ratio: float = config.stop_loss_pct / 100.0
        self._tp_ratio: float = config.take_profit_pct / 100.0
        self._daily_ratio: float = config.max_daily_loss_pct / 100.0
        self._max_pos_ratio: float = config.max_position_pct_per_symbol / 100.0

        self._allow_long: bool = config.allow_long
        self._allow_short: bool = config.allow_short

        self._leverage_default: int = leverage_default
        self._leverage_max: int = leverage_max

        # 일일 추적용 기준 자산
        self._today_start_equity: float = initial_equity

    # ── 레버리지 ────────────────────────────────────────────────

    def resolve_leverage(self, signal_leverage: "int | None") -> int:
        """
        신호 제안 레버리지를 유효 레버리지로 정규화.
        min(signal or default, max), 최소 1.
        """
        base = signal_leverage if signal_leverage is not None else self._leverage_default
        return max(1, min(base, self._leverage_max))

    # ── 증거금 계산 ─────────────────────────────────────────────

    def calc_margin(
        self,
        free_balance: float,
        equity: float,
        symbol: str,
        positions: "dict[str, Position]",
        prices: "dict[str, float]",
    ) -> float:
        """
        진입 증거금 계산.

        단순화 규칙:
          available = free_balance * size_ratio
          cap       = equity * max_position_ratio  (심볼당 명목 한도를 증거금 단위로 근사)
          margin    = min(available, cap)

        실제 바이낸스 명목 한도 계산(명목=qty*price/leverage)은 복잡하므로
        equity 비율 캡으로 근사한다.
        """
        available = free_balance * self._size_ratio
        cap = equity * self._max_pos_ratio
        margin = min(available, cap)
        return max(0.0, margin)

    # ── 손절 / 익절 판정 ────────────────────────────────────────

    def check_stop_loss(
        self,
        side: PositionSide,
        entry_price: float,
        current_price: float,
    ) -> bool:
        """
        손절 조건.
        롱: (current - entry) / entry <= -sl_ratio
        숏: (entry - current) / entry <= -sl_ratio
        """
        if entry_price <= 0.0:
            return False
        if side == PositionSide.LONG:
            return (current_price - entry_price) / entry_price <= -self._sl_ratio
        else:
            return (entry_price - current_price) / entry_price <= -self._sl_ratio

    def check_take_profit(
        self,
        side: PositionSide,
        entry_price: float,
        current_price: float,
    ) -> bool:
        """
        익절 조건. tp_ratio <= 0 이면 항상 False (비활성).
        롱: (current - entry) / entry >= tp_ratio
        숏: (entry - current) / entry >= tp_ratio
        """
        if self._tp_ratio <= 0.0 or entry_price <= 0.0:
            return False
        if side == PositionSide.LONG:
            return (current_price - entry_price) / entry_price >= self._tp_ratio
        else:
            return (entry_price - current_price) / entry_price >= self._tp_ratio

    # ── 일일 손실 한도 ───────────────────────────────────────────

    def check_daily_loss_limit(
        self,
        today_start_equity: float,
        current_equity: float,
    ) -> bool:
        """
        일일 손실 한도 초과 여부.
        (start - current) / start >= daily_ratio
        """
        if today_start_equity <= 0.0:
            return False
        return (today_start_equity - current_equity) / today_start_equity >= self._daily_ratio

    # ── 신호 검증 / 강등 ────────────────────────────────────────

    def validate_signal(
        self,
        signal: Signal,
        symbol: str,
        wallet: PaperWallet,
        prices: "dict[str, float]",
        today_start_equity: float,
    ) -> Signal:
        """
        신호 유효성 검증. 규칙 위반 시 HOLD 로 강등.
        - CLOSE / HOLD 는 무조건 통과.
        - LONG: allow_long=False 또는 일일한도 초과 시 → HOLD
        - SHORT: allow_short=False 또는 일일한도 초과 시 → HOLD
        """
        if signal.type in (SignalType.CLOSE, SignalType.HOLD):
            return signal

        current_equity = wallet.get_equity(prices)
        daily_limit_hit = self.check_daily_loss_limit(today_start_equity, current_equity)

        if signal.type == SignalType.LONG:
            if not self._allow_long:
                logger.info("[%s] LONG → HOLD (allow_long=False)", symbol)
                return Signal(
                    type=SignalType.HOLD,
                    symbol=signal.symbol,
                    price=signal.price,
                    reason="allow_long=False",
                )
            if daily_limit_hit:
                logger.info("[%s] LONG → HOLD (일일 손실 한도 도달)", symbol)
                return Signal(
                    type=SignalType.HOLD,
                    symbol=signal.symbol,
                    price=signal.price,
                    reason="daily_loss_limit",
                )

        elif signal.type == SignalType.SHORT:
            if not self._allow_short:
                logger.info("[%s] SHORT → HOLD (allow_short=False)", symbol)
                return Signal(
                    type=SignalType.HOLD,
                    symbol=signal.symbol,
                    price=signal.price,
                    reason="allow_short=False",
                )
            if daily_limit_hit:
                logger.info("[%s] SHORT → HOLD (일일 손실 한도 도달)", symbol)
                return Signal(
                    type=SignalType.HOLD,
                    symbol=signal.symbol,
                    price=signal.price,
                    reason="daily_loss_limit",
                )

        return signal

    # ── 일일 추적 초기화 ────────────────────────────────────────

    def reset_daily_tracking(self, current_equity: float) -> None:
        """매일 00:00 KST 호출. 기준 자산을 현재값으로 갱신."""
        self._today_start_equity = current_equity
        logger.info("일일 추적 초기화 | today_start_equity=%.2f", current_equity)
