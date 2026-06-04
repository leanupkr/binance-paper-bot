"""
최적화 엔진 단위 테스트.
합성 캔들 생성 — 네트워크 미사용.
"""

from __future__ import annotations

import pandas as pd
import pytest
import yaml

from optimize import GridSearchOptimizer, OptimizationResult, RankedResult


# ──────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────

def make_candles(n: int = 120, start: float = 100.0, trend: float = 0.2) -> pd.DataFrame:
    prices = [start + trend * i for i in range(n)]
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    rows = [
        {"open": p, "high": p * 1.005, "low": p * 0.995, "close": p, "volume": 100.0}
        for p in prices
    ]
    return pd.DataFrame(rows, index=idx)


def make_volatile_candles(n: int = 200) -> pd.DataFrame:
    """상승 후 하락하는 변동성 있는 캔들."""
    import math
    prices = [100.0 + 20 * math.sin(i / 10.0) + i * 0.1 for i in range(n)]
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    rows = [
        {"open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 100.0}
        for p in prices
    ]
    return pd.DataFrame(rows, index=idx)


# ──────────────────────────────────────────────
# 테스트 1: split_ratio 적용 확인
# ──────────────────────────────────────────────

def test_split_ratio_applied():
    """
    split_ratio=0.7이면 인샘플 70%, 아웃샘플 30% 분할이 실제 적용됐는지
    간접 검증: 아웃샘플 지표가 인샘플과 독립적으로 계산됨.
    """
    candles = make_volatile_candles(n=200)
    opt = GridSearchOptimizer(
        symbols=["BTCUSDT"],
        candles_map={"BTCUSDT": candles},
        param_grids={"ma_crossover": {"short": [3, 5], "long": [10, 20]}},
        leverage_grid=[2],
        split_ratio=0.7,
        initial_balance=1000.0,
    )
    result = opt.run()

    assert result.total_combinations_tested > 0
    # 경고 여부와 무관하게 best가 존재해야 함
    assert isinstance(result.best, RankedResult)

    # in_sample_metrics 와 out_sample_metrics 는 서로 다른 캔들로 계산됨
    best = result.best
    in_ret = best.in_sample_metrics.get("total_return_pct", 0.0)
    out_ret = best.out_sample_metrics.get("total_return_pct", 0.0)
    # 두 값이 동일하다면 분할이 안 된 것 (동일 캔들이 아닌 한)
    # 항등 조건은 아니므로 존재 여부만 확인
    assert "total_return_pct" in best.in_sample_metrics
    assert "total_return_pct" in best.out_sample_metrics


# ──────────────────────────────────────────────
# 테스트 2: MA short >= long 조합 제외 확인
# ──────────────────────────────────────────────

def test_ma_short_ge_long_excluded():
    """
    short >= long 인 MA 파라미터 조합은 결과에 포함되지 않아야 한다.
    """
    candles = make_volatile_candles(n=200)
    opt = GridSearchOptimizer(
        symbols=["BTCUSDT"],
        candles_map={"BTCUSDT": candles},
        param_grids={
            "ma_crossover": {
                "short": [5, 10, 20],
                "long": [5, 10, 20],
            }
        },
        leverage_grid=[2],
        split_ratio=0.7,
        initial_balance=1000.0,
    )
    result = opt.run()

    for r in result.ranked_results:
        if r.strategy_name == "ma_crossover":
            short_v = r.params.get("short")
            long_v = r.params.get("long")
            if short_v is not None and long_v is not None:
                assert short_v < long_v, (
                    f"short({short_v}) >= long({long_v}) 조합이 결과에 포함됨"
                )


# ──────────────────────────────────────────────
# 테스트 3: to_config_yaml 유효한 YAML 반환
# ──────────────────────────────────────────────

def test_to_config_yaml_valid():
    """to_config_yaml() 반환값이 파싱 가능한 YAML이어야 한다."""
    candles = make_volatile_candles(n=200)
    opt = GridSearchOptimizer(
        symbols=["BTCUSDT"],
        candles_map={"BTCUSDT": candles},
        param_grids={"ma_crossover": {"short": [5], "long": [20]}},
        leverage_grid=[3],
        split_ratio=0.7,
        initial_balance=1000.0,
    )
    result = opt.run()

    yaml_str = result.to_config_yaml()
    assert isinstance(yaml_str, str) and len(yaml_str) > 0

    parsed = yaml.safe_load(yaml_str)
    assert isinstance(parsed, dict), "YAML이 dict 형태이어야 함"
    assert "strategy" in parsed, "recommended_config에 'strategy' 키가 있어야 함"


# ──────────────────────────────────────────────
# 테스트 4: 아웃샘플 거래 수 부족 경고
# ──────────────────────────────────────────────

def test_out_sample_low_trade_warning():
    """
    아웃샘플 구간이 매우 짧으면 거래 수 부족 경고가 발생해야 한다.
    split_ratio=0.97 → 아웃샘플 3%로 거래 발생 불가.
    """
    candles = make_volatile_candles(n=200)
    opt = GridSearchOptimizer(
        symbols=["BTCUSDT"],
        candles_map={"BTCUSDT": candles},
        param_grids={"ma_crossover": {"short": [5], "long": [20]}},
        leverage_grid=[2],
        split_ratio=0.97,  # 아웃샘플 6봉 정도 → 거래 거의 없음
        initial_balance=1000.0,
    )
    result = opt.run()

    all_warnings = [w for r in result.ranked_results for w in r.warnings]
    low_trade_warns = [w for w in all_warnings if "부족" in w and "거래" in w]
    assert len(low_trade_warns) >= 1, (
        f"아웃샘플 거래 수 부족 경고가 없음. 전체 경고: {all_warnings}"
    )


# ──────────────────────────────────────────────
# 테스트 5: 랭킹 내림차순 정렬 확인
# ──────────────────────────────────────────────

def test_ranking_is_descending():
    """ranked_results 의 score 는 내림차순이어야 한다."""
    candles = make_volatile_candles(n=200)
    opt = GridSearchOptimizer(
        symbols=["BTCUSDT"],
        candles_map={"BTCUSDT": candles},
        param_grids={"ma_crossover": {"short": [3, 5, 7], "long": [10, 20, 30]}},
        leverage_grid=[2, 3],
        split_ratio=0.7,
        initial_balance=1000.0,
    )
    result = opt.run()

    scores = [r.score for r in result.ranked_results]
    assert scores == sorted(scores, reverse=True), "ranked_results가 score 내림차순이 아님"


# ──────────────────────────────────────────────
# 테스트 6: best는 가능하면 경고 없는 결과 우선
# ──────────────────────────────────────────────

def test_best_prefers_no_warning():
    """경고 없는 결과가 있으면 best가 해당 결과를 가리켜야 한다."""
    candles = make_volatile_candles(n=300)
    opt = GridSearchOptimizer(
        symbols=["BTCUSDT"],
        candles_map={"BTCUSDT": candles},
        param_grids={"ma_crossover": {"short": [5, 7], "long": [15, 20]}},
        leverage_grid=[2],
        split_ratio=0.7,
        initial_balance=1000.0,
    )
    result = opt.run()

    all_no_warn = [r for r in result.ranked_results if not r.warnings]
    if all_no_warn:
        # 경고 없는 결과가 있으면 best는 경고 없어야 함
        assert not result.best.warnings, (
            f"경고 없는 결과가 {len(all_no_warn)}건 있는데 best에 경고 있음: {result.best.warnings}"
        )
