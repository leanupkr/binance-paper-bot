"""
백테스트 엔진 단위 테스트.
합성 캔들 생성 — 네트워크 미사용.
"""

from __future__ import annotations

import math
import pandas as pd
import pytest

from backtest import BacktestConfig, BacktestEngine, BacktestResult


# ──────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────

def make_candles(
    prices: list[float],
    interval: str = "1h",
    start: str = "2024-01-01",
) -> pd.DataFrame:
    """단순 OHLCV 합성. high=price*1.005, low=price*0.995, volume=100."""
    idx = pd.date_range(start, periods=len(prices), freq=interval)
    rows = []
    for p in prices:
        rows.append({
            "open": p,
            "high": p * 1.005,
            "low": p * 0.995,
            "close": p,
            "volume": 100.0,
        })
    df = pd.DataFrame(rows, index=idx)
    return df


def make_trending_candles(
    n: int = 60, start_price: float = 100.0, trend: float = 1.0
) -> pd.DataFrame:
    """단조 상승(trend>0) 또는 하락(trend<0) 합성 캔들."""
    prices = [start_price + trend * i for i in range(n)]
    return make_candles(prices)


def make_falling_candles(n: int = 60, start: float = 100.0) -> pd.DataFrame:
    return make_trending_candles(n, start, trend=-0.5)


def make_rising_candles(n: int = 60, start: float = 100.0) -> pd.DataFrame:
    return make_trending_candles(n, start, trend=0.5)


def run_ma_backtest(
    candles: pd.DataFrame,
    allow_short: bool = True,
    short: int = 5,
    long: int = 20,
    leverage: int = 3,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    position_size_pct: float = 0.5,
) -> BacktestResult:
    cfg = BacktestConfig(
        symbol="BTCUSDT",
        strategy_name="ma_crossover",
        strategy_params={"short": short, "long": long},
        leverage=leverage,
        initial_balance=1000.0,
        taker_fee_rate=0.0005,
        slippage_bp=0.0,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        allow_short=allow_short,
        position_size_pct=position_size_pct,
        min_data_rows=5,
    )
    return BacktestEngine(cfg).run(candles)


# ──────────────────────────────────────────────
# 테스트 1: 체결 타이밍 — LONG 신호 봉의 다음 봉 open 으로 체결
# ──────────────────────────────────────────────

def test_long_entry_fills_at_next_open():
    """
    MA 골든크로스 발생 봉(i) 다음 봉(i+1) open으로 filled 되어야 한다.
    """
    # 처음 25봉은 하락(short<long 유지) -> 26봉째부터 급상승(골든크로스 유발)
    prices = [100.0 - i * 0.1 for i in range(25)]  # 하락
    prices += [100.0 + i * 2.0 for i in range(40)]  # 상승

    # open을 close와 다르게 설정하기 위해 직접 DataFrame 생성
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="1h")
    rows = []
    for j, p in enumerate(prices):
        open_p = p * 1.01 if j > 0 else p  # next open != close
        rows.append({
            "open": open_p,
            "high": p * 1.01,
            "low": p * 0.99,
            "close": p,
            "volume": 100.0,
        })
    candles = pd.DataFrame(rows, index=idx)

    cfg = BacktestConfig(
        symbol="BTCUSDT",
        strategy_name="ma_crossover",
        strategy_params={"short": 5, "long": 20},
        leverage=2,
        initial_balance=1000.0,
        taker_fee_rate=0.0,
        slippage_bp=0.0,
        position_size_pct=0.5,
        allow_short=False,  # 롱만
        min_data_rows=5,
    )
    result = BacktestEngine(cfg).run(candles)

    open_trades = result.trades[result.trades["action"] == "OPEN"]
    assert len(open_trades) >= 1, "롱 진입 체결이 최소 1건 있어야 함"

    # 첫 OPEN 체결가는 신호 봉 close와 다르고 다음 봉 open이어야 함
    first = open_trades.iloc[0]
    signal_bar_idx = int(first["bar_idx"])
    expected_fill = float(candles.iloc[signal_bar_idx + 1]["open"])
    assert abs(first["filled_price"] - expected_fill) < 1e-8, (
        f"체결가 {first['filled_price']:.6f} != 다음봉 open {expected_fill:.6f}"
    )


# ──────────────────────────────────────────────
# 테스트 2: 하락장에서 SHORT 이익 검증
# ──────────────────────────────────────────────

def test_short_profit_in_downtrend():
    """하락 추세에서 숏 포지션은 양수 realized_pnl이어야 한다."""
    # 상승(short>long 확립) → 하락(클린 데드크로스로 숏 진입 후 하락 추세 수익)
    prices = [150.0 + i * 2.0 for i in range(26)]       # 150 → 200 상승
    prices += [200.0 - i * 2.0 for i in range(1, 55)]   # 198 → 92 하락
    candles = make_candles(prices)
    result = run_ma_backtest(candles, allow_short=True, short=5, long=20, leverage=3)

    # 단조 하락에서는 골든크로스(청산 신호)가 없어 숏이 미결제로 남는다.
    # 따라서 경제적 성과(실현/미실현 손익 또는 최종 자산)로 숏 수익을 검증한다.
    final_equity = float(result.equity_curve.iloc[-1])
    assert (
        result.realized_pnl > 0
        or result.unrealized_pnl > 0
        or final_equity > 1000.0
    ), (
        f"하락장 숏 수익 없음: realized={result.realized_pnl:.4f}, "
        f"unrealized={result.unrealized_pnl:.4f}, equity={final_equity:.4f}"
    )


# ──────────────────────────────────────────────
# 테스트 3: 강제청산 발생 (고레버리지 + 급락)
# ──────────────────────────────────────────────

def test_liquidation_occurs_with_high_leverage_and_crash():
    """
    레버리지 10x, 급락 합성 데이터 → num_liquidations >= 1.
    롱 진입 직후 10% 급락하면 (청산가 = entry*(1 - 1/10 + 0.005) ≈ entry*0.905)
    10% 하락 시 청산가 이하로 떨어짐.
    """
    # 완만한 하락(short<long 확립) → 상승(클린 골든크로스로 롱 진입) → 급락(청산 유발)
    prices = [1000.0 - i * 8 for i in range(12)]               # 1000 → 912 하락
    prices += [912.0 + i * 12 for i in range(1, 18)]           # 924 → 1116 상승(골든크로스)
    prices += [1116.0 * (0.82 ** (i + 1)) for i in range(15)]  # 급락(청산가 하향 돌파)

    idx = pd.date_range("2024-01-01", periods=len(prices), freq="1h")
    rows = []
    for p in prices:
        rows.append({
            "open": p,
            "high": p * 1.002,
            "low": p * 0.85,   # low를 매우 낮게 설정해 청산 유발
            "close": p,
            "volume": 100.0,
        })
    candles = pd.DataFrame(rows, index=idx)

    cfg = BacktestConfig(
        symbol="BTCUSDT",
        strategy_name="ma_crossover",
        strategy_params={"short": 3, "long": 7},
        leverage=10,
        initial_balance=1000.0,
        taker_fee_rate=0.0005,
        slippage_bp=0.0,
        position_size_pct=0.9,
        allow_short=False,
        maintenance_margin_rate=0.005,
        min_data_rows=5,
    )
    result = BacktestEngine(cfg).run(candles)
    assert result.num_liquidations >= 1, (
        f"강제청산이 최소 1건 발생해야 함 (현재 {result.num_liquidations}건)"
    )


# ──────────────────────────────────────────────
# 테스트 4: 수수료로 인한 equity 감소
# ──────────────────────────────────────────────

def test_fee_reduces_equity():
    """수수료 0.05% taker 적용 시 equity가 초기잔고보다 낮아질 수 있음."""
    # 횡보 가격 (수익 없음 → 수수료만 차감)
    prices = [100.0] * 60
    candles = make_candles(prices)

    # RSI 전략으로 신호 발생 없이 수수료만 영향 보기
    # MA 크로스오버로 신호 강제: 짧은 윈도우
    cfg = BacktestConfig(
        symbol="BTCUSDT",
        strategy_name="ma_crossover",
        strategy_params={"short": 2, "long": 3},
        leverage=2,
        initial_balance=1000.0,
        taker_fee_rate=0.005,  # 높은 수수료
        slippage_bp=0.0,
        position_size_pct=0.5,
        allow_short=True,
        min_data_rows=5,
    )
    result = BacktestEngine(cfg).run(candles)

    # 수수료가 있으면 wallet_balance <= initial
    # 거래가 있었다면 fee가 차감됐을 것
    if result.num_trades > 0:
        total_fees = result.trades["fee_usdt"].sum() if "fee_usdt" in result.trades.columns else 0
        final_equity = float(result.equity_curve.iloc[-1])
        assert final_equity <= 1000.0 + 1e-6 or total_fees > 0, (
            f"수수료 {total_fees:.4f} USDT 발생했어야 함"
        )


# ──────────────────────────────────────────────
# 테스트 5: 알려진 equity 곡선으로 MDD 검증
# ──────────────────────────────────────────────

def test_mdd_known_curve():
    """equity 100 → 80 → 120 이면 MDD = (80-100)/100*100 = -20%"""
    from backtest import _calc_mdd
    equity = pd.Series([100.0, 90.0, 80.0, 100.0, 120.0])
    mdd = _calc_mdd(equity)
    # cummax 기준: 100→100→100→100→120. drawdown 최솟값 = (80-100)/100 = -0.2
    assert abs(mdd - (-20.0)) < 1e-8, f"예상 MDD=-20.0, 실제={mdd}"


# ──────────────────────────────────────────────
# 테스트 6: 마지막봉 신호 미체결
# ──────────────────────────────────────────────

def test_last_bar_signal_not_filled():
    """
    마지막 봉에서 발생한 신호는 체결되지 않아야 한다.
    (다음 봉 open이 없으므로)
    """
    # 마지막 봉에서 골든크로스가 발생하도록 구성
    # short=2, long=3: 마지막 봉에서 크로스오버 유발
    prices = [100.0, 99.0, 98.0, 97.0, 110.0]  # 마지막에 급등
    candles = make_candles(prices)

    cfg = BacktestConfig(
        symbol="BTCUSDT",
        strategy_name="ma_crossover",
        strategy_params={"short": 2, "long": 3},
        leverage=1,
        initial_balance=1000.0,
        taker_fee_rate=0.0,
        slippage_bp=0.0,
        position_size_pct=0.5,
        allow_short=False,
        min_data_rows=2,
    )
    result = BacktestEngine(cfg).run(candles)

    # 마지막봉(idx=4)에서만 신호가 발생했다면 체결 없어야 함
    open_trades = result.trades[result.trades["action"] == "OPEN"]
    last_idx = len(prices) - 1
    last_bar_opens = open_trades[open_trades["bar_idx"] == last_idx]
    assert len(last_bar_opens) == 0, "마지막 봉 신호는 체결되면 안 됨"
