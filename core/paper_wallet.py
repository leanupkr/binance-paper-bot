"""
바이낸스 USDT-M 선물 페이퍼 트레이딩 체결 엔진.
백테스트와 실시간 오케스트레이터가 공유하는 핵심 모듈.
실거래 API 호출 없음 — 내부 가상 지갑(AccountState)만 변경.
"""

import copy
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 열거형
# ──────────────────────────────────────────────

class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


# ──────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────

@dataclass
class Position:
    symbol: str
    side: PositionSide
    quantity: float
    entry_price: float
    leverage: int
    margin_usdt: float
    liquidation_price: float


@dataclass
class AccountState:
    balance_usdt: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class OpenResult:
    symbol: str
    side: PositionSide
    filled_price: float
    quantity: float
    notional: float
    fee_usdt: float
    margin_usdt: float
    liquidation_price: float
    new_entry_price: float
    timestamp: float


@dataclass
class CloseResult:
    symbol: str
    side: PositionSide
    filled_price: float
    quantity: float
    gross_pnl: float
    fee_usdt: float
    realized_pnl: float
    returned_margin: float
    timestamp: float
    is_liquidation: bool = False


@dataclass
class ExecutionResult:
    success: bool
    action: str  # "OPEN" | "CLOSE"
    detail: "OpenResult | CloseResult | None" = None
    rejection_reason: str = ""


# ──────────────────────────────────────────────
# 순수 함수 계산 엔진 (부수효과 없음)
# ──────────────────────────────────────────────

class ExecutionEngine:
    """모든 메서드는 @staticmethod 순수 함수 — 외부 상태 미참조."""

    @staticmethod
    def liquidation_price(
        side: PositionSide,
        entry_price: float,
        leverage: int,
        maintenance_margin_rate: float = 0.005,
    ) -> float:
        """
        격리 마진 청산가 (수수료 무시 간이식).
        롱: entry * (1 - 1/leverage + mmr)
        숏: entry * (1 + 1/leverage - mmr)
        """
        mmr = maintenance_margin_rate
        if side == PositionSide.LONG:
            return entry_price * (1.0 - 1.0 / leverage + mmr)
        else:
            return entry_price * (1.0 + 1.0 / leverage - mmr)

    @staticmethod
    def unrealized_pnl(
        side: PositionSide,
        entry_price: float,
        mark_price: float,
        quantity: float,
    ) -> float:
        """미실현 손익. 롱=(mark-entry)*qty, 숏=(entry-mark)*qty."""
        if side == PositionSide.LONG:
            return (mark_price - entry_price) * quantity
        else:
            return (entry_price - mark_price) * quantity

    @staticmethod
    def calc_weighted_avg_price(
        existing_qty: float,
        existing_avg: float,
        new_qty: float,
        new_price: float,
    ) -> float:
        """증액 시 가중평균 진입가."""
        total_qty = existing_qty + new_qty
        if total_qty <= 0.0:
            return new_price
        return (existing_qty * existing_avg + new_qty * new_price) / total_qty

    @staticmethod
    def calculate_open(
        symbol: str,
        side: PositionSide,
        mark_price: float,
        margin_usdt: float,
        leverage: int,
        taker_fee_rate: float = 0.0005,
        slippage_bp: float = 0.0,
        maintenance_margin_rate: float = 0.005,
    ) -> OpenResult:
        """
        진입 체결 계산 (순수 함수).
        filled: 롱=price*(1+slip/1e4), 숏=price*(1-slip/1e4)
        notional=margin*leverage; quantity=notional/filled; fee=notional*fee_rate
        """
        slip = slippage_bp / 1e4
        if side == PositionSide.LONG:
            filled_price = mark_price * (1.0 + slip)
        else:
            filled_price = mark_price * (1.0 - slip)

        notional = margin_usdt * leverage
        quantity = notional / filled_price
        fee_usdt = notional * taker_fee_rate
        liq_price = ExecutionEngine.liquidation_price(
            side, filled_price, leverage, maintenance_margin_rate
        )

        return OpenResult(
            symbol=symbol,
            side=side,
            filled_price=filled_price,
            quantity=quantity,
            notional=notional,
            fee_usdt=fee_usdt,
            margin_usdt=margin_usdt,
            liquidation_price=liq_price,
            new_entry_price=filled_price,
            timestamp=time.time(),
        )

    @staticmethod
    def calculate_close(
        symbol: str,
        side: PositionSide,
        mark_price: float,
        quantity: float,
        entry_price: float,
        allocated_margin: float,
        taker_fee_rate: float = 0.0005,
        slippage_bp: float = 0.0,
        is_liquidation: bool = False,
    ) -> CloseResult:
        """
        청산 체결 계산 (순수 함수).
        close filled: 롱(매도)=price*(1-slip), 숏(매수)=price*(1+slip)
        gross_pnl: 롱=(filled-entry)*qty, 숏=(entry-filled)*qty
        fee=qty*filled*fee_rate
        realized=gross_pnl-fee
        """
        slip = slippage_bp / 1e4
        if side == PositionSide.LONG:
            filled_price = mark_price * (1.0 - slip)
            gross_pnl = (filled_price - entry_price) * quantity
        else:
            filled_price = mark_price * (1.0 + slip)
            gross_pnl = (entry_price - filled_price) * quantity

        fee_usdt = quantity * filled_price * taker_fee_rate
        realized_pnl = gross_pnl - fee_usdt

        return CloseResult(
            symbol=symbol,
            side=side,
            filled_price=filled_price,
            quantity=quantity,
            gross_pnl=gross_pnl,
            fee_usdt=fee_usdt,
            realized_pnl=realized_pnl,
            returned_margin=allocated_margin,
            timestamp=time.time(),
            is_liquidation=is_liquidation,
        )


# ──────────────────────────────────────────────
# 상태 보유 지갑
# ──────────────────────────────────────────────

class PaperWallet:
    """
    바이낸스 USDT-M 선물 가상 지갑.
    격리 마진, 롱/숏 양방향, 증액(같은 방향), 반대전환 거부.
    """

    MIN_NOTIONAL_USDT: float = 5.0

    def __init__(
        self,
        initial_balance_usdt: float,
        taker_fee_rate: float = 0.0005,
        slippage_bp: float = 0.0,
        maintenance_margin_rate: float = 0.005,
        max_leverage: int = 10,
        state: "AccountState | None" = None,
    ) -> None:
        self.taker_fee_rate = taker_fee_rate
        self.slippage_bp = slippage_bp
        self.maintenance_margin_rate = maintenance_margin_rate
        self.max_leverage = max_leverage

        if state is not None:
            self.state = state
        else:
            self.state = AccountState(balance_usdt=initial_balance_usdt)

    # ── 진입 ──────────────────────────────────

    def open(
        self,
        symbol: str,
        side: PositionSide,
        price: float,
        margin_usdt: float,
        leverage: int,
    ) -> ExecutionResult:
        """
        포지션 진입 또는 증액.
        같은 방향 보유: 가중평균 entry, qty/margin 합산, leverage=새 요청값 유지, 청산가 재계산.
        반대 방향 보유: OPPOSITE_POSITION_EXISTS 거부.
        """
        # 유효성 검증
        if price <= 0.0:
            logger.warning("[%s] INVALID_PRICE: %.6f", symbol, price)
            return ExecutionResult(success=False, action="OPEN", rejection_reason="INVALID_PRICE")

        if margin_usdt <= 0.0:
            logger.warning("[%s] INVALID_MARGIN: %.6f", symbol, margin_usdt)
            return ExecutionResult(success=False, action="OPEN", rejection_reason="INVALID_MARGIN")

        if leverage > self.max_leverage:
            logger.warning("[%s] LEVERAGE_EXCEEDED: %d > %d", symbol, leverage, self.max_leverage)
            return ExecutionResult(success=False, action="OPEN", rejection_reason="LEVERAGE_EXCEEDED")

        # 슬리피지 적용 filled 가격으로 명목가치 계산 (검증 목적)
        slip = self.slippage_bp / 1e4
        filled_price = price * (1.0 + slip) if side == PositionSide.LONG else price * (1.0 - slip)
        notional = margin_usdt * leverage

        if notional < self.MIN_NOTIONAL_USDT:
            logger.warning("[%s] MIN_NOTIONAL_NOT_MET: %.2f < %.2f", symbol, notional, self.MIN_NOTIONAL_USDT)
            return ExecutionResult(success=False, action="OPEN", rejection_reason="MIN_NOTIONAL_NOT_MET")

        fee = notional * self.taker_fee_rate
        required = margin_usdt + fee

        if required > self.state.balance_usdt:
            logger.warning("[%s] INSUFFICIENT_BALANCE: need %.6f, have %.6f", symbol, required, self.state.balance_usdt)
            return ExecutionResult(success=False, action="OPEN", rejection_reason="INSUFFICIENT_BALANCE")

        # 반대 방향 포지션 확인
        existing = self.state.positions.get(symbol)
        if existing is not None and existing.side != side:
            logger.warning("[%s] OPPOSITE_POSITION_EXISTS", symbol)
            return ExecutionResult(success=False, action="OPEN", rejection_reason="OPPOSITE_POSITION_EXISTS")

        # ExecutionEngine으로 체결 계산
        result = ExecutionEngine.calculate_open(
            symbol=symbol,
            side=side,
            mark_price=price,
            margin_usdt=margin_usdt,
            leverage=leverage,
            taker_fee_rate=self.taker_fee_rate,
            slippage_bp=self.slippage_bp,
            maintenance_margin_rate=self.maintenance_margin_rate,
        )

        # 잔고 차감
        self.state.balance_usdt -= (margin_usdt + result.fee_usdt)

        if existing is not None:
            # 증액: 가중평균 entry, qty/margin 합산
            # leverage는 새 요청값으로 유지, 청산가는 새 entry/leverage로 재계산
            new_entry = ExecutionEngine.calc_weighted_avg_price(
                existing.quantity, existing.entry_price,
                result.quantity, result.filled_price,
            )
            new_qty = existing.quantity + result.quantity
            new_margin = existing.margin_usdt + margin_usdt
            new_liq = ExecutionEngine.liquidation_price(
                side, new_entry, leverage, self.maintenance_margin_rate
            )
            result.new_entry_price = new_entry
            result.liquidation_price = new_liq

            self.state.positions[symbol] = Position(
                symbol=symbol,
                side=side,
                quantity=new_qty,
                entry_price=new_entry,
                leverage=leverage,
                margin_usdt=new_margin,
                liquidation_price=new_liq,
            )
        else:
            # 신규 포지션
            self.state.positions[symbol] = Position(
                symbol=symbol,
                side=side,
                quantity=result.quantity,
                entry_price=result.filled_price,
                leverage=leverage,
                margin_usdt=margin_usdt,
                liquidation_price=result.liquidation_price,
            )

        logger.info(
            "[%s] OPEN %s | filled=%.4f qty=%.6f margin=%.2f lev=%d liq=%.4f",
            symbol, side.value, result.filled_price, result.quantity,
            margin_usdt, leverage, result.liquidation_price,
        )
        return ExecutionResult(success=True, action="OPEN", detail=result)

    # ── 청산 ──────────────────────────────────

    def close(
        self,
        symbol: str,
        price: float,
        quantity: "float | None" = None,
        is_liquidation: bool = False,
    ) -> ExecutionResult:
        """
        포지션 청산 (전량 또는 부분).
        부분청산 시 margin/qty 비례 차감, entry/leverage/청산가 유지.
        전량(잔여<1e-10) 시 positions 키 삭제.
        """
        pos = self.state.positions.get(symbol)
        if pos is None:
            logger.warning("[%s] NO_POSITION", symbol)
            return ExecutionResult(success=False, action="CLOSE", rejection_reason="NO_POSITION")

        if price <= 0.0:
            logger.warning("[%s] INVALID_PRICE: %.6f", symbol, price)
            return ExecutionResult(success=False, action="CLOSE", rejection_reason="INVALID_PRICE")

        close_qty = quantity if quantity is not None else pos.quantity

        if close_qty > pos.quantity + 1e-10:
            logger.warning("[%s] INSUFFICIENT_QUANTITY: %.6f > %.6f", symbol, close_qty, pos.quantity)
            return ExecutionResult(success=False, action="CLOSE", rejection_reason="INSUFFICIENT_QUANTITY")

        # 부분청산 시 비례 증거금
        margin_ratio = close_qty / pos.quantity
        allocated_margin = pos.margin_usdt * margin_ratio

        result = ExecutionEngine.calculate_close(
            symbol=symbol,
            side=pos.side,
            mark_price=price,
            quantity=close_qty,
            entry_price=pos.entry_price,
            allocated_margin=allocated_margin,
            taker_fee_rate=self.taker_fee_rate,
            slippage_bp=self.slippage_bp,
            is_liquidation=is_liquidation,
        )

        # 잔고 반환: returned_margin + realized_pnl
        self.state.balance_usdt += result.returned_margin + result.realized_pnl
        self.state.realized_pnl += result.realized_pnl

        # 포지션 업데이트
        remaining_qty = pos.quantity - close_qty
        if remaining_qty < 1e-10:
            # 전량 청산
            del self.state.positions[symbol]
        else:
            # 부분 청산: entry/leverage/청산가 유지
            self.state.positions[symbol] = Position(
                symbol=pos.symbol,
                side=pos.side,
                quantity=remaining_qty,
                entry_price=pos.entry_price,
                leverage=pos.leverage,
                margin_usdt=pos.margin_usdt - allocated_margin,
                liquidation_price=pos.liquidation_price,
            )

        logger.info(
            "[%s] CLOSE %s | filled=%.4f qty=%.6f pnl=%.4f liq=%s",
            symbol, pos.side.value, result.filled_price, close_qty,
            result.realized_pnl, is_liquidation,
        )
        return ExecutionResult(success=True, action="CLOSE", detail=result)

    # ── 강제청산 판정 ──────────────────────────

    def check_liquidations(self, prices: dict[str, float]) -> list[ExecutionResult]:
        """
        보유 포지션 강제청산 판정.
        롱: mark_price <= liq_price, 숏: mark_price >= liq_price.
        청산가(liq_price)로 강제청산 처리.
        """
        results: list[ExecutionResult] = []
        # 순회 중 dict 변경 방지
        for symbol in list(self.state.positions.keys()):
            pos = self.state.positions.get(symbol)
            if pos is None:
                continue
            mark = prices.get(symbol)
            if mark is None:
                continue

            triggered = False
            if pos.side == PositionSide.LONG and mark <= pos.liquidation_price:
                triggered = True
            elif pos.side == PositionSide.SHORT and mark >= pos.liquidation_price:
                triggered = True

            if triggered:
                logger.warning(
                    "[%s] LIQUIDATION triggered | mark=%.4f liq=%.4f side=%s",
                    symbol, mark, pos.liquidation_price, pos.side.value,
                )
                r = self.close(symbol, pos.liquidation_price, is_liquidation=True)
                results.append(r)

        return results

    # ── 조회 ──────────────────────────────────

    def get_position(self, symbol: str) -> "Position | None":
        return self.state.positions.get(symbol)

    def get_balance(self) -> float:
        """가용(free) 잔고."""
        return self.state.balance_usdt

    def get_wallet_balance(self) -> float:
        """free + 포지션 증거금 합."""
        used = sum(p.margin_usdt for p in self.state.positions.values())
        return self.state.balance_usdt + used

    def get_equity(self, prices: dict[str, float]) -> float:
        """wallet_balance + 미실현손익 합."""
        upnl = sum(self.get_unrealized_pnl(prices).values())
        return self.get_wallet_balance() + upnl

    def get_unrealized_pnl(self, prices: dict[str, float]) -> dict[str, float]:
        result: dict[str, float] = {}
        for symbol, pos in self.state.positions.items():
            mark = prices.get(symbol)
            if mark is None:
                result[symbol] = 0.0
                continue
            result[symbol] = ExecutionEngine.unrealized_pnl(
                pos.side, pos.entry_price, mark, pos.quantity
            )
        return result

    def snapshot(self) -> AccountState:
        """AccountState 깊은 복사 반환."""
        return copy.deepcopy(self.state)
