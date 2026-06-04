"""
core/orchestrator.py — 실시간 페이퍼 트레이딩 오케스트레이터.
바이낸스 USDT-M 선물. 실거래 API 호출 없음.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from core.config import AppConfig
from core.market_data import BinanceFuturesClient
from core.notifier import NotifyEvent, create_notifier
from core.paper_wallet import PaperWallet, PositionSide
from core.risk import RiskManager
from core.storage_factory import create_storage
from strategies.base import SignalType, StrategyRegistry

if TYPE_CHECKING:
    from core.paper_wallet import ExecutionResult
    from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

# 계좌 상태 저장 주기 (tick 횟수)
_SAVE_STATE_EVERY_N_TICKS = 10


def _now_kst() -> datetime:
    return datetime.now(_KST)


class Orchestrator:
    """실시간 루프 제어. SIGINT/SIGTERM 수신 시 정상 종료."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._shutdown_flag = False
        self._tick_count = 0
        self._consecutive_failures = 0

        # 의존성 초기화
        self._client = BinanceFuturesClient(
            request_interval_sec=config.request_interval_sec,
        )
        # create_storage 로 STORAGE_BACKEND 환경변수에 따라 sqlite/supabase 선택
        self._storage = create_storage(config)
        self._storage.initialize()
        self._notifier = create_notifier(config.slack)

        # 전략 등록 확인을 위해 전략 모듈 import (레지스트리 채우기)
        self._import_strategies()

        self._strategy: BaseStrategy = StrategyRegistry.create(
            config.strategy,
            config.strategy_params.get(config.strategy, {}),
        )

        # 지갑: DB에서 복원 또는 신규 생성
        saved_state = self._storage.load_account_state()
        if saved_state is not None:
            logger.info("계좌 상태 복원 | balance=%.2f", saved_state.balance_usdt)
            self._wallet = PaperWallet(
                initial_balance_usdt=config.initial_balance_usdt,
                taker_fee_rate=config.taker_fee_rate,
                slippage_bp=config.slippage_bp,
                maintenance_margin_rate=config.maintenance_margin_rate,
                max_leverage=config.leverage_max,
                state=saved_state,
            )
        else:
            self._wallet = PaperWallet(
                initial_balance_usdt=config.initial_balance_usdt,
                taker_fee_rate=config.taker_fee_rate,
                slippage_bp=config.slippage_bp,
                maintenance_margin_rate=config.maintenance_margin_rate,
                max_leverage=config.leverage_max,
            )

        initial_equity = self._wallet.get_equity({})
        self._risk = RiskManager(
            config=config.risk,
            initial_equity=initial_equity,
            leverage_default=config.leverage_default,
            leverage_max=config.leverage_max,
        )

        # 일일 경계 추적 (KST)
        self._last_day_kst: int = _now_kst().date().toordinal()
        self._today_start_equity: float = initial_equity

    @staticmethod
    def _import_strategies() -> None:
        """전략 모듈을 import 해서 StrategyRegistry 에 등록."""
        import strategies.ma_crossover          # noqa: F401
        import strategies.rsi_mean_reversion    # noqa: F401
        import strategies.volatility_breakout   # noqa: F401

    # ── 공개 진입점 ────────────────────────────────────────────────────────────

    def run_once(self) -> None:
        """단일 틱 실행(GitHub Actions/cron 용).

        시그널 핸들러·무한루프 없이 _check_day_rollover() + _tick() 1회 실행 후
        account_state 저장, equity 기록, storage.close() 를 수행한다.
        예외를 잡아 로깅 — 틱 1회가 죽지 않도록 보장.
        GitHub Actions 에서 매 호출마다 Orchestrator(cfg) 를 새로 생성하면
        load_account_state() 로 상태가 복원되어 stateless 하게 동작한다.
        """
        try:
            self._check_day_rollover()
            self._tick()
        except Exception as exc:
            logger.exception("run_once _tick 예외 (계속 진행): %s", exc)

        # 틱 이후 계좌 스냅샷 저장 (equity 는 _tick 에서 이미 기록됨 — 중복 방지)
        try:
            self._storage.save_account_state(self._wallet.snapshot(), mode=self._config.mode)
        except Exception as exc:
            logger.exception("run_once 상태 저장 예외 (계속 진행): %s", exc)
        finally:
            self._storage.close()

        logger.info("run_once 완료")

    def run(self) -> None:
        """메인 루프. SIGINT/SIGTERM 수신 시 종료."""
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self._notifier.notify(
            NotifyEvent.ON_BOT_START,
            {
                "mode": self._config.mode,
                "strategy": self._config.strategy,
                "symbols": self._config.symbols,
                "initial_balance_usdt": self._wallet.get_balance(),
            },
        )
        logger.info(
            "봇 시작 | mode=%s strategy=%s symbols=%s leverage=%d balance=%.2f USDT",
            self._config.mode,
            self._config.strategy,
            self._config.symbols,
            self._config.leverage_default,
            self._wallet.get_balance(),
        )

        while not self._shutdown_flag:
            tick_start = time.monotonic()
            try:
                self._check_day_rollover()
                self._tick()
                self._consecutive_failures = 0
            except Exception as exc:
                self._consecutive_failures += 1
                logger.exception("_tick 예외 (연속실패=%d): %s", self._consecutive_failures, exc)
                if self._consecutive_failures >= self._config.max_consecutive_failures:
                    self._notifier.notify(
                        NotifyEvent.ON_ERROR,
                        {"message": str(exc), "context": f"연속 실패 {self._consecutive_failures}회"},
                    )

            if self._shutdown_flag:
                break

            # 경과 보정 sleep
            elapsed = time.monotonic() - tick_start
            remaining = self._config.poll_interval_sec - elapsed
            if remaining > 0:
                time.sleep(remaining)

        self._shutdown()

    # ── 시그널 핸들러 ──────────────────────────────────────────────────────────

    def _handle_signal(self, signum: int, frame: object) -> None:
        logger.info("종료 시그널 수신 (signum=%d)", signum)
        self._shutdown_flag = True

    # ── 일일 경계 처리 ─────────────────────────────────────────────────────────

    def _check_day_rollover(self) -> None:
        """KST 자정을 지나면 일일 추적 초기화 + 요약 알림."""
        today = _now_kst().date().toordinal()
        if today <= self._last_day_kst:
            return

        self._last_day_kst = today
        prices = self._client.get_mark_price(self._config.symbols)
        current_equity = self._wallet.get_equity(prices)

        self._risk.reset_daily_tracking(current_equity)
        self._today_start_equity = current_equity

        report = self._storage.get_performance_report()
        self._notifier.notify(
            NotifyEvent.ON_DAILY_SUMMARY,
            {
                "equity": round(current_equity, 4),
                "realized_pnl": round(self._wallet.state.realized_pnl, 4),
                "trade_count": report.get("trade_count", 0),
                "win_rate_pct": round(report.get("win_rate_pct", 0.0), 2),
                "mdd_pct": round(report.get("mdd_pct", 0.0), 2),
            },
        )
        logger.info("일일 롤오버 | current_equity=%.2f", current_equity)

    # ── 한 틱 처리 ─────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._tick_count += 1
        symbols = self._config.symbols

        # 1) 마크 가격 수집
        prices = self._client.get_mark_price(symbols)
        if not prices:
            prices = self._client.get_ticker(symbols)
        if not prices:
            logger.warning("가격 수집 실패 — tick 스킵")
            return

        # 2) 강제청산 판정
        liq_results = self._wallet.check_liquidations(prices)
        for liq_r in liq_results:
            if liq_r.success and liq_r.detail is not None:
                detail = liq_r.detail
                sym = detail.symbol if hasattr(detail, "symbol") else "?"
                eq = self._wallet.get_equity(prices)
                self._storage.record_trade(
                    result=liq_r,
                    symbol=sym,
                    trigger="LIQUIDATION",
                    strategy_name=self._strategy.name,
                    mark_price=prices.get(sym, 0.0),
                    equity_after=eq,
                )
                self._notifier.notify(
                    NotifyEvent.ON_LIQUIDATION,
                    {
                        "symbol": sym,
                        "side": getattr(detail, "side", "?"),
                        "liquidation_price": getattr(detail, "filled_price", "?"),
                        "realized_pnl": round(getattr(detail, "realized_pnl", 0.0), 4),
                    },
                )

        # 3) 심볼별 손절/익절 + 전략 신호 처리
        for symbol in symbols:
            price = prices.get(symbol)
            if price is None:
                continue

            pos = self._wallet.get_position(symbol)

            # 3a) 보유 포지션 손절/익절 판정
            if pos is not None:
                sl_hit = self._risk.check_stop_loss(pos.side, pos.entry_price, price)
                tp_hit = self._risk.check_take_profit(pos.side, pos.entry_price, price)

                if sl_hit or tp_hit:
                    trigger = "STOP_LOSS" if sl_hit else "TAKE_PROFIT"
                    event = NotifyEvent.ON_STOP_LOSS if sl_hit else NotifyEvent.ON_TAKE_PROFIT
                    close_r = self._wallet.close(symbol, price)
                    if close_r.success and close_r.detail is not None:
                        eq = self._wallet.get_equity(prices)
                        self._storage.record_trade(
                            result=close_r,
                            symbol=symbol,
                            trigger=trigger,
                            strategy_name=self._strategy.name,
                            mark_price=price,
                            equity_after=eq,
                        )
                        rpnl = getattr(close_r.detail, "realized_pnl", 0.0)
                        self._notifier.notify(
                            event,
                            {
                                "symbol": symbol,
                                "side": pos.side.value,
                                "entry_price": round(pos.entry_price, 4),
                                "current_price": round(price, 4),
                                "realized_pnl": round(rpnl, 4),
                            },
                        )
                    # 손절/익절 후 이번 틱 이 심볼은 진입 신호 처리 생략
                    continue

            # 3b) 캔들 수집 + 전략 신호
            candles = self._client.get_ohlcv(
                symbol,
                interval=self._config.candle_interval,
                count=self._config.candle_count,
            )
            if candles.empty:
                logger.warning("[%s] 캔들 빈 데이터 — 신호 스킵", symbol)
                continue

            signal = self._strategy.on_data(symbol, candles, price)

            # 신호 기록 (HOLD 포함)
            self._storage.record_signal(
                symbol=symbol,
                strategy_name=self._strategy.name,
                action=signal.type.value,
                price=price,
                indicators=signal.indicators,
            )

            # 리스크 검증
            signal = self._risk.validate_signal(
                signal=signal,
                symbol=symbol,
                wallet=self._wallet,
                prices=prices,
                today_start_equity=self._today_start_equity,
            )

            # 3c) 신호 실행
            if signal.type in (SignalType.LONG, SignalType.SHORT):
                target_side = (
                    PositionSide.LONG
                    if signal.type == SignalType.LONG
                    else PositionSide.SHORT
                )
                current_pos = self._wallet.get_position(symbol)

                # 반대 포지션 보유 시 먼저 청산
                if current_pos is not None and current_pos.side != target_side:
                    close_r = self._wallet.close(symbol, price)
                    if close_r.success and close_r.detail is not None:
                        eq = self._wallet.get_equity(prices)
                        self._storage.record_trade(
                            result=close_r,
                            symbol=symbol,
                            trigger="STRATEGY",
                            strategy_name=self._strategy.name,
                            mark_price=price,
                            equity_after=eq,
                        )
                        rpnl = getattr(close_r.detail, "realized_pnl", 0.0)
                        self._notifier.notify(
                            NotifyEvent.ON_CLOSE,
                            {
                                "symbol": symbol,
                                "side": current_pos.side.value,
                                "filled_price": round(getattr(close_r.detail, "filled_price", price), 4),
                                "realized_pnl": round(rpnl, 4),
                            },
                        )

                # 같은 방향 포지션을 이미 보유 중이면 추가 진입(피라미딩) 생략
                if self._wallet.get_position(symbol) is not None:
                    continue

                # 진입
                leverage = self._risk.resolve_leverage(signal.leverage)
                free_bal = self._wallet.get_balance()
                equity = self._wallet.get_equity(prices)
                margin = self._risk.calc_margin(
                    free_balance=free_bal,
                    equity=equity,
                    symbol=symbol,
                    positions=self._wallet.state.positions,
                    prices=prices,
                )
                if margin <= 0.0:
                    logger.info("[%s] 증거금 0 — 진입 스킵", symbol)
                    continue

                open_r = self._wallet.open(symbol, target_side, price, margin, leverage)
                if open_r.success and open_r.detail is not None:
                    eq = self._wallet.get_equity(prices)
                    self._storage.record_trade(
                        result=open_r,
                        symbol=symbol,
                        trigger="STRATEGY",
                        strategy_name=self._strategy.name,
                        mark_price=price,
                        equity_after=eq,
                    )
                    d = open_r.detail
                    self._notifier.notify(
                        NotifyEvent.ON_OPEN,
                        {
                            "symbol": symbol,
                            "side": target_side.value,
                            "leverage": leverage,
                            "entry_price": round(d.new_entry_price, 4),
                            "filled_price": round(d.filled_price, 4),
                            "margin_usdt": round(d.margin_usdt, 4),
                            "liquidation_price": round(d.liquidation_price, 4),
                        },
                    )
                else:
                    logger.info(
                        "[%s] 진입 거부: %s", symbol, open_r.rejection_reason
                    )

            elif signal.type == SignalType.CLOSE:
                current_pos = self._wallet.get_position(symbol)
                if current_pos is not None:
                    close_r = self._wallet.close(symbol, price)
                    if close_r.success and close_r.detail is not None:
                        eq = self._wallet.get_equity(prices)
                        self._storage.record_trade(
                            result=close_r,
                            symbol=symbol,
                            trigger="STRATEGY",
                            strategy_name=self._strategy.name,
                            mark_price=price,
                            equity_after=eq,
                        )
                        rpnl = getattr(close_r.detail, "realized_pnl", 0.0)
                        self._notifier.notify(
                            NotifyEvent.ON_CLOSE,
                            {
                                "symbol": symbol,
                                "side": current_pos.side.value,
                                "filled_price": round(getattr(close_r.detail, "filled_price", price), 4),
                                "realized_pnl": round(rpnl, 4),
                            },
                        )

        # 4) equity curve 기록
        prices_snap = prices
        free_bal = self._wallet.get_balance()
        wallet_bal = self._wallet.get_wallet_balance()
        used_margin = wallet_bal - free_bal
        upnl_map = self._wallet.get_unrealized_pnl(prices_snap)
        total_upnl = sum(upnl_map.values())
        total_equity = self._wallet.get_equity(prices_snap)

        self._storage.record_equity(
            total_equity=total_equity,
            balance_usdt=free_bal,
            used_margin=used_margin,
            unrealized_pnl=total_upnl,
        )

        # 5) 주기적 계좌 상태 저장
        if self._tick_count % _SAVE_STATE_EVERY_N_TICKS == 0:
            self._storage.save_account_state(self._wallet.snapshot(), mode=self._config.mode)

    # ── 종료 처리 ──────────────────────────────────────────────────────────────

    def _shutdown(self) -> None:
        logger.info("봇 종료 처리 시작")
        prices = self._client.get_mark_price(self._config.symbols)
        if not prices:
            prices = self._client.get_ticker(self._config.symbols)

        equity = self._wallet.get_equity(prices)
        self._storage.record_equity(
            total_equity=equity,
            balance_usdt=self._wallet.get_balance(),
            used_margin=self._wallet.get_wallet_balance() - self._wallet.get_balance(),
            unrealized_pnl=sum(self._wallet.get_unrealized_pnl(prices).values()),
        )
        self._storage.save_account_state(self._wallet.snapshot(), mode=self._config.mode)

        report = self._storage.get_performance_report()
        self._notifier.notify(
            NotifyEvent.ON_BOT_STOP,
            {
                "equity": round(equity, 4),
                "realized_pnl": round(self._wallet.state.realized_pnl, 4),
                "trade_count": report.get("trade_count", 0),
            },
        )
        self._storage.close()
        logger.info("봇 종료 완료 | final_equity=%.2f USDT", equity)
