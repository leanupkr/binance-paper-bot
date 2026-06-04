"""
바이낸스 USDT-M 선물 백테스트 엔진.
룩어헤드 방지: i봉까지 슬라이스로 신호 생성 -> i+1봉 open으로 체결.
인트라바 판정(우선순위: 청산 > 손절 > 익절).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from core.paper_wallet import (
    ExecutionResult,
    PaperWallet,
    PositionSide,
)
from strategies.base import BaseStrategy, Signal, SignalType, StrategyRegistry

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    symbol: str
    strategy_name: str
    strategy_params: dict
    leverage: int = 3
    initial_balance: float = 1000.0
    taker_fee_rate: float = 0.0005
    slippage_bp: float = 0.0
    maintenance_margin_rate: float = 0.005
    position_size_pct: float = 1.0          # 0~1, 가용잔고 대비 증거금 비율
    stop_loss_pct: Optional[float] = None   # None=비활성
    take_profit_pct: Optional[float] = None
    allow_short: bool = True
    min_data_rows: int = 30


@dataclass
class BacktestResult:
    symbol: str
    strategy_name: str
    params: dict
    leverage: int
    total_return_pct: float
    num_trades: int
    win_rate: float
    avg_profit_loss_ratio: float
    mdd: float                          # 최대낙폭 %, 음수
    sharpe_ratio: float
    num_liquidations: int
    realized_pnl: float
    unrealized_pnl: float
    equity_curve: pd.Series
    trades: pd.DataFrame
    warnings: list[str]


class BacktestEngine:
    """시계열 캔들 데이터로 전략을 시뮬레이션하는 백테스트 엔진."""

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config

    def run(self, candles: pd.DataFrame) -> BacktestResult:
        cfg = self.config
        warnings: list[str] = []

        if len(candles) < cfg.min_data_rows:
            warnings.append(
                f"데이터 부족: {len(candles)} < min_data_rows({cfg.min_data_rows})"
            )

        strategy: BaseStrategy = StrategyRegistry.create(
            cfg.strategy_name, cfg.strategy_params
        )

        wallet = PaperWallet(
            initial_balance_usdt=cfg.initial_balance,
            taker_fee_rate=cfg.taker_fee_rate,
            slippage_bp=cfg.slippage_bp,
            maintenance_margin_rate=cfg.maintenance_margin_rate,
            max_leverage=max(cfg.leverage, 1),
        )

        equity_records: dict[pd.Timestamp, float] = {}
        trade_records: list[dict] = []
        num_liquidations: int = 0
        symbol = cfg.symbol

        n = len(candles)

        for i in range(n):
            row = candles.iloc[i]
            close_price = float(row["close"])

            # ── 인트라바 판정 (포지션 보유 시) ──────────────────────
            pos = wallet.get_position(symbol)
            intrabar_triggered = False

            if pos is not None:
                # 불리한 극값: 롱=봉 저가, 숏=봉 고가
                if pos.side == PositionSide.LONG:
                    adverse_price = float(row["low"])
                else:
                    adverse_price = float(row["high"])

                # 1) 청산 판정
                liq_price = pos.liquidation_price
                is_liquidation = (
                    pos.side == PositionSide.LONG and adverse_price <= liq_price
                ) or (
                    pos.side == PositionSide.SHORT and adverse_price >= liq_price
                )

                if is_liquidation:
                    r = wallet.close(symbol, liq_price, is_liquidation=True)
                    if r.success and r.detail is not None:
                        num_liquidations += 1
                        trade_records.append(
                            _make_trade_record(r, symbol, "LIQUIDATION", i)
                        )
                    intrabar_triggered = True

                # 2) 손절 판정
                elif cfg.stop_loss_pct is not None and cfg.stop_loss_pct > 0:
                    sl_ratio = cfg.stop_loss_pct / 100.0
                    if pos.side == PositionSide.LONG:
                        sl_price = pos.entry_price * (1.0 - sl_ratio)
                        triggered = adverse_price <= sl_price
                    else:
                        sl_price = pos.entry_price * (1.0 + sl_ratio)
                        triggered = adverse_price >= sl_price

                    if triggered:
                        r = wallet.close(symbol, sl_price)
                        if r.success and r.detail is not None:
                            trade_records.append(
                                _make_trade_record(r, symbol, "STOP_LOSS", i)
                            )
                        intrabar_triggered = True

                # 3) 익절 판정
                elif (
                    cfg.take_profit_pct is not None
                    and cfg.take_profit_pct > 0
                ):
                    tp_ratio = cfg.take_profit_pct / 100.0
                    if pos.side == PositionSide.LONG:
                        tp_price = pos.entry_price * (1.0 + tp_ratio)
                        # 롱 익절: 봉 고가가 목표 이상이면 체결 가능
                        triggered = float(row["high"]) >= tp_price
                    else:
                        tp_price = pos.entry_price * (1.0 - tp_ratio)
                        # 숏 익절: 봉 저가가 목표 이하이면 체결 가능
                        triggered = float(row["low"]) <= tp_price

                    if triggered:
                        r = wallet.close(symbol, tp_price)
                        if r.success and r.detail is not None:
                            trade_records.append(
                                _make_trade_record(r, symbol, "TAKE_PROFIT", i)
                            )
                        intrabar_triggered = True

            # ── 전략 신호 (인트라바 미트리거 + 마지막봉 아님) ──────
            if not intrabar_triggered and i < n - 1:
                signal: Signal = strategy.on_data(
                    symbol, candles.iloc[: i + 1], close_price
                )

                # 체결 가격: 다음 봉 open
                exec_price = float(candles.iloc[i + 1]["open"])

                if signal.type in (SignalType.LONG, SignalType.SHORT):
                    target_side = (
                        PositionSide.LONG
                        if signal.type == SignalType.LONG
                        else PositionSide.SHORT
                    )

                    # 반대 포지션이면 먼저 청산
                    cur_pos = wallet.get_position(symbol)
                    if cur_pos is not None and cur_pos.side != target_side:
                        r = wallet.close(symbol, exec_price)
                        if r.success and r.detail is not None:
                            trade_records.append(
                                _make_trade_record(r, symbol, "STRATEGY", i)
                            )
                        cur_pos = wallet.get_position(symbol)  # 청산 후 재조회(None)

                    # 같은 방향 포지션을 이미 보유 중이면 추가 진입(피라미딩) 안 함
                    if cur_pos is not None:
                        pass
                    # 숏 불허 시 숏 진입 생략
                    elif target_side == PositionSide.SHORT and not cfg.allow_short:
                        pass
                    else:
                        free = wallet.get_balance()
                        raw_margin = free * cfg.position_size_pct
                        # 수수료 여유 확보: margin + notional*fee <= free 가 되도록 상한 적용
                        # (position_size_pct=1.0 일 때 수수료 때문에 전량 거부되는 문제 방지)
                        max_affordable = free / (1.0 + cfg.leverage * cfg.taker_fee_rate)
                        margin = min(raw_margin, max_affordable) * 0.999
                        if margin > 0:
                            r = wallet.open(
                                symbol, target_side, exec_price, margin, cfg.leverage
                            )
                            if r.success and r.detail is not None:
                                trade_records.append(
                                    _make_trade_record(r, symbol, "STRATEGY", i)
                                )

                elif signal.type == SignalType.CLOSE:
                    cur_pos = wallet.get_position(symbol)
                    if cur_pos is not None:
                        r = wallet.close(symbol, exec_price)
                        if r.success and r.detail is not None:
                            trade_records.append(
                                _make_trade_record(r, symbol, "STRATEGY", i)
                            )

            # ── 봉 종료 equity 기록 ──────────────────────────────
            ts = candles.index[i]
            equity = wallet.get_equity({symbol: close_price})
            equity_records[ts] = equity

        # ── 미결 포지션 미실현손익 ──────────────────────────────────
        last_price = float(candles.iloc[-1]["close"])
        unrealized_pnl_map = wallet.get_unrealized_pnl({symbol: last_price})
        unrealized_pnl = sum(unrealized_pnl_map.values())

        # ── 지표 계산 ────────────────────────────────────────────
        equity_series = pd.Series(equity_records)
        result = _compute_metrics(
            equity_series=equity_series,
            trade_records=trade_records,
            initial_balance=cfg.initial_balance,
            num_liquidations=num_liquidations,
            realized_pnl=wallet.state.realized_pnl,
            unrealized_pnl=unrealized_pnl,
            symbol=symbol,
            strategy_name=cfg.strategy_name,
            params=cfg.strategy_params,
            leverage=cfg.leverage,
            warnings=warnings,
        )
        return result


# ──────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────

def _make_trade_record(
    result: ExecutionResult, symbol: str, trigger: str, bar_idx: int
) -> dict:
    d = result.detail
    rec: dict = {
        "bar_idx": bar_idx,
        "symbol": symbol,
        "action": result.action,
        "trigger": trigger,
    }
    if d is not None:
        rec["filled_price"] = getattr(d, "filled_price", 0.0)
        rec["quantity"] = getattr(d, "quantity", 0.0)
        rec["fee_usdt"] = getattr(d, "fee_usdt", 0.0)
        rec["realized_pnl"] = getattr(d, "realized_pnl", 0.0)
        rec["is_liquidation"] = getattr(d, "is_liquidation", False)
    return rec


def _compute_metrics(
    equity_series: pd.Series,
    trade_records: list[dict],
    initial_balance: float,
    num_liquidations: int,
    realized_pnl: float,
    unrealized_pnl: float,
    symbol: str,
    strategy_name: str,
    params: dict,
    leverage: int,
    warnings: list[str],
) -> BacktestResult:
    # 수익률
    final_equity = float(equity_series.iloc[-1]) if len(equity_series) > 0 else initial_balance
    total_return_pct = (final_equity - initial_balance) / initial_balance * 100.0

    # 거래 DataFrame
    trades_df = pd.DataFrame(trade_records) if trade_records else pd.DataFrame(
        columns=["bar_idx", "symbol", "action", "trigger",
                 "filled_price", "quantity", "fee_usdt", "realized_pnl", "is_liquidation"]
    )

    # 청산 거래(CLOSE action)만 win_rate/ratio 계산
    close_trades = trades_df[trades_df["action"] == "CLOSE"] if len(trades_df) > 0 else trades_df
    num_trades = len(close_trades)

    if num_trades > 0:
        wins = close_trades[close_trades["realized_pnl"] > 0]["realized_pnl"]
        losses = close_trades[close_trades["realized_pnl"] <= 0]["realized_pnl"]
        win_rate = len(wins) / num_trades
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = abs(float(losses.mean())) if len(losses) > 0 else 0.0
        avg_profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf")
    else:
        win_rate = 0.0
        avg_profit_loss_ratio = 0.0

    # MDD
    mdd = _calc_mdd(equity_series)

    # 샤프
    sharpe = _calc_sharpe(equity_series)

    return BacktestResult(
        symbol=symbol,
        strategy_name=strategy_name,
        params=params,
        leverage=leverage,
        total_return_pct=total_return_pct,
        num_trades=num_trades,
        win_rate=win_rate,
        avg_profit_loss_ratio=avg_profit_loss_ratio,
        mdd=mdd,
        sharpe_ratio=sharpe,
        num_liquidations=num_liquidations,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        equity_curve=equity_series,
        trades=trades_df,
        warnings=warnings,
    )


def _calc_mdd(equity: pd.Series) -> float:
    """MDD % — min((V - cummax) / cummax) * 100. 음수 반환."""
    if len(equity) < 2:
        return 0.0
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax * 100.0
    return float(drawdown.min())


def _calc_sharpe(equity: pd.Series, annual_factor: float = 365.0) -> float:
    """일별 수익률 기반 연환산 샤프. 데이터 부족 시 0 반환."""
    if len(equity) < 3:
        return 0.0
    returns = equity.pct_change().dropna()
    if len(returns) < 2:
        return 0.0
    std = float(returns.std())
    if std == 0.0 or math.isnan(std):
        return 0.0
    mean = float(returns.mean())
    return float(mean / std * math.sqrt(annual_factor))


# ──────────────────────────────────────────────
# CLI 진입점 — 바이낸스 실데이터 백테스트
# ──────────────────────────────────────────────

def _print_result(res: BacktestResult) -> None:
    """백테스트 결과를 콘솔에 요약 출력."""
    print(
        f"\n[{res.symbol}] {res.strategy_name} {res.params} lev={res.leverage}x"
    )
    print(f"  누적 수익률   : {res.total_return_pct:+.2f}%")
    print(f"  실현 손익     : {res.realized_pnl:+.2f} USDT")
    print(f"  미실현 손익   : {res.unrealized_pnl:+.2f} USDT")
    print(f"  거래 횟수     : {res.num_trades} (청산 {res.num_liquidations}건)")
    print(f"  승률          : {res.win_rate * 100:.1f}%")
    ratio = res.avg_profit_loss_ratio
    ratio_s = "inf" if ratio == float("inf") else f"{ratio:.2f}"
    print(f"  평균 손익비   : {ratio_s}")
    print(f"  MDD           : {res.mdd:.2f}%")
    print(f"  샤프 비율     : {res.sharpe_ratio:.3f}")
    for w in res.warnings:
        print(f"  ⚠️  {w}")


def main() -> None:
    import argparse

    from core.config import load_config
    from core.market_data import BinanceFuturesClient

    parser = argparse.ArgumentParser(description="바이낸스 USDT-M 선물 백테스트")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--symbol", default=None, help="단일 심볼(기본: config 전체)")
    parser.add_argument("--strategy", default=None, help="전략명(기본: config.strategy)")
    parser.add_argument("--days", type=int, default=None, help="과거 데이터 기간(일)")
    parser.add_argument("--interval", default=None, help="캔들 인터벌(기본: config)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    strategy = args.strategy or cfg.strategy
    symbols = [args.symbol] if args.symbol else cfg.symbols
    days = args.days or int(cfg.backtest.get("days", 365))
    interval = args.interval or cfg.candle_interval
    initial_balance = float(cfg.backtest.get("initial_balance_usdt", cfg.initial_balance_usdt))
    risk = cfg.risk

    client = BinanceFuturesClient(request_interval_sec=cfg.request_interval_sec)

    print(
        f"백테스트: {strategy} | 심볼 {symbols} | {interval} | {days}일 | "
        f"레버리지 {cfg.leverage_default}x | 시작 {initial_balance} USDT"
    )

    returns: list[float] = []
    for symbol in symbols:
        candles = client.fetch_historical_candles(symbol, days, interval)
        if candles.empty:
            print(f"\n[{symbol}] 캔들 수집 실패 — 건너뜀")
            continue
        bt_cfg = BacktestConfig(
            symbol=symbol,
            strategy_name=strategy,
            strategy_params=cfg.strategy_params.get(strategy, {}),
            leverage=cfg.leverage_default,
            initial_balance=initial_balance,
            taker_fee_rate=cfg.taker_fee_rate,
            slippage_bp=cfg.slippage_bp,
            maintenance_margin_rate=cfg.maintenance_margin_rate,
            position_size_pct=risk.position_size_pct / 100.0,
            stop_loss_pct=risk.stop_loss_pct if risk.stop_loss_pct > 0 else None,
            take_profit_pct=risk.take_profit_pct if risk.take_profit_pct > 0 else None,
            allow_short=risk.allow_short,
            min_data_rows=30,
        )
        result = BacktestEngine(bt_cfg).run(candles)
        print(f"  ({len(candles)}봉 수집)")
        _print_result(result)
        returns.append(result.total_return_pct)

    # ── 포트폴리오 요약 (심볼별 독립 백테스트 동일가중 평균) ──────────
    if returns:
        avg = sum(returns) / len(returns)
        wins = sum(1 for r in returns if r > 0)
        print("\n" + "=" * 52)
        print(
            f"  포트폴리오 수익률(동일가중 평균) : {avg:+.2f}%  "
            f"[{wins}/{len(returns)} 심볼 흑자]"
        )
        print("=" * 52)


if __name__ == "__main__":
    main()
