"""
그리드 서치 파라미터 최적화.
(전략 × 심볼 × 파라미터 조합 × 레버리지)를 70/30 시계열 분할로 in/out-sample 평가.
스코어 = 0.5*z(out_sharpe) + 0.3*z(out_return) - 0.2*z(out_mdd)
"""

from __future__ import annotations

import concurrent.futures
import itertools
import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import yaml

from backtest import BacktestConfig, BacktestEngine, BacktestResult
from strategies.base import StrategyRegistry

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 결과 데이터클래스
# ──────────────────────────────────────────────

@dataclass
class RankedResult:
    rank: int
    symbol: str
    strategy_name: str
    params: dict
    leverage: int
    score: float
    in_sample_metrics: dict
    out_sample_metrics: dict
    warnings: list[str] = field(default_factory=list)


@dataclass
class OptimizationResult:
    ranked_results: list[RankedResult]
    best: RankedResult
    recommended_config: dict
    total_combinations_tested: int

    def to_config_yaml(self) -> str:
        """recommended_config 를 YAML 문자열로 반환."""
        return yaml.dump(self.recommended_config, allow_unicode=True, default_flow_style=False)

    def print_ranking_table(self, top_n: int = 10) -> None:
        """상위 top_n 결과를 테이블 형식으로 출력."""
        top = self.ranked_results[:top_n]
        header = (
            f"{'Rank':>4}  {'Symbol':<10}  {'Strategy':<22}  {'Params':<30}  "
            f"{'Lev':>3}  {'Score':>7}  {'OutSharpe':>9}  {'OutRet%':>8}  {'OutMDD%':>8}  {'Warns':>5}"
        )
        print(header)
        print("-" * len(header))
        for r in top:
            out = r.out_sample_metrics
            params_str = str(r.params)[:28]
            print(
                f"{r.rank:>4}  {r.symbol:<10}  {r.strategy_name:<22}  {params_str:<30}  "
                f"{r.leverage:>3}  {r.score:>7.3f}  "
                f"{out.get('sharpe_ratio', 0.0):>9.3f}  "
                f"{out.get('total_return_pct', 0.0):>8.2f}  "
                f"{out.get('mdd', 0.0):>8.2f}  "
                f"{len(r.warnings):>5}"
            )


# ──────────────────────────────────────────────
# 그리드 서치 최적화
# ──────────────────────────────────────────────

class GridSearchOptimizer:
    """
    candles_map: {symbol: DataFrame}
    param_grids: {strategy_name: {param_name: [values]}}
    leverage_grid: 탐색할 레버리지 목록
    score_weights: (sharpe, return, mdd) 가중치 튜플
    split_ratio: 인샘플 비율 (0~1)
    """

    def __init__(
        self,
        symbols: list[str],
        candles_map: dict[str, Any],
        param_grids: dict[str, dict[str, list]],
        leverage_grid: list[int],
        split_ratio: float = 0.7,
        score_weights: tuple[float, float, float] = (0.5, 0.3, 0.2),
        initial_balance: float = 1000.0,
    ) -> None:
        self.symbols = symbols
        self.candles_map = candles_map
        self.param_grids = param_grids
        self.leverage_grid = leverage_grid
        self.split_ratio = split_ratio
        self.score_weights = score_weights
        self.initial_balance = initial_balance

    def run(self) -> OptimizationResult:
        # 전략 목록: param_grids 에 있는 것 + 레지스트리에 등록된 것 교집합
        strategy_names = [
            s for s in self.param_grids.keys()
            if s in StrategyRegistry.list_strategies()
        ]

        tasks: list[tuple[str, str, dict, int]] = []

        for strategy_name in strategy_names:
            grid = self.param_grids.get(strategy_name, {})
            param_combos = _expand_param_grid(strategy_name, grid)

            for symbol in self.symbols:
                for params in param_combos:
                    for lev in self.leverage_grid:
                        tasks.append((strategy_name, symbol, params, lev))

        total = len(tasks)
        logger.info("최적화 총 %d 조합 시작", total)

        raw_results: list[dict] = []

        # 조합 수가 많으면 병렬 처리 (단, 작은 경우에도 정상 동작)
        if total <= 8:
            for t in tasks:
                res = self._eval_one(*t)
                if res is not None:
                    raw_results.append(res)
        else:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = {executor.submit(self._eval_one, *t): t for t in tasks}
                for fut in concurrent.futures.as_completed(futures):
                    res = fut.result()
                    if res is not None:
                        raw_results.append(res)

        if not raw_results:
            # 최소한 빈 결과라도 반환
            dummy = RankedResult(
                rank=1, symbol="", strategy_name="", params={},
                leverage=1, score=0.0, in_sample_metrics={}, out_sample_metrics={},
                warnings=["결과 없음"],
            )
            return OptimizationResult(
                ranked_results=[dummy], best=dummy,
                recommended_config={}, total_combinations_tested=total,
            )

        ranked = _rank_results(raw_results, self.score_weights)
        best = ranked[0]
        recommended = _build_recommended_config(ranked)

        return OptimizationResult(
            ranked_results=ranked,
            best=best,
            recommended_config=recommended,
            total_combinations_tested=total,
        )

    def _eval_one(
        self,
        strategy_name: str,
        symbol: str,
        params: dict,
        leverage: int,
    ) -> dict | None:
        candles = self.candles_map.get(symbol)
        if candles is None or len(candles) < 10:
            return None

        split_idx = max(1, int(len(candles) * self.split_ratio))
        in_candles = candles.iloc[:split_idx]
        out_candles = candles.iloc[split_idx:]

        if len(out_candles) < 5:
            return None

        warnings: list[str] = []

        in_res = _run_bt(strategy_name, params, leverage, symbol, in_candles, self.initial_balance)
        out_res = _run_bt(strategy_name, params, leverage, symbol, out_candles, self.initial_balance)

        if in_res is None or out_res is None:
            return None

        warnings.extend(in_res.warnings)
        warnings.extend(out_res.warnings)

        # 과최적화 경고
        in_sharpe = in_res.sharpe_ratio
        out_sharpe = out_res.sharpe_ratio
        if abs(in_sharpe) > 1e-9:
            degradation = (in_sharpe - out_sharpe) / abs(in_sharpe)
            if degradation > 0.5:
                warnings.append(
                    f"과최적화 의심: in_sharpe={in_sharpe:.3f} → out_sharpe={out_sharpe:.3f} (열화>{50}%)"
                )

        if out_res.num_trades < 5:
            warnings.append(f"아웃샘플 거래 수 부족: {out_res.num_trades} < 5")

        if out_res.num_trades > 0:
            liq_ratio = out_res.num_liquidations / out_res.num_trades
            if liq_ratio > 0.3:
                warnings.append(
                    f"청산 과다: {out_res.num_liquidations}/{out_res.num_trades} ({liq_ratio:.0%})"
                )

        return {
            "strategy_name": strategy_name,
            "symbol": symbol,
            "params": params,
            "leverage": leverage,
            "in": _result_to_metrics(in_res),
            "out": _result_to_metrics(out_res),
            "warnings": warnings,
        }


# ──────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────

def _expand_param_grid(strategy_name: str, grid: dict[str, list]) -> list[dict]:
    """파라미터 그리드를 조합 목록으로 확장. MA는 short<long만."""
    if not grid:
        return [{}]

    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    combos: list[dict] = []

    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))
        # MA 크로스오버 제약: short < long
        if strategy_name == "ma_crossover":
            short_val = params.get("short")
            long_val = params.get("long")
            if short_val is not None and long_val is not None:
                if short_val >= long_val:
                    continue
        combos.append(params)

    return combos if combos else [{}]


def _run_bt(
    strategy_name: str,
    params: dict,
    leverage: int,
    symbol: str,
    candles: Any,
    initial_balance: float,
) -> BacktestResult | None:
    try:
        cfg = BacktestConfig(
            symbol=symbol,
            strategy_name=strategy_name,
            strategy_params=params,
            leverage=leverage,
            initial_balance=initial_balance,
            taker_fee_rate=0.0005,
            slippage_bp=0.0,
            maintenance_margin_rate=0.005,
            position_size_pct=1.0,
            allow_short=True,
        )
        engine = BacktestEngine(cfg)
        return engine.run(candles)
    except Exception as e:
        logger.debug("백테스트 실패 [%s %s %s lev=%d]: %s", strategy_name, symbol, params, leverage, e)
        return None


def _result_to_metrics(res: BacktestResult) -> dict:
    return {
        "total_return_pct": res.total_return_pct,
        "num_trades": res.num_trades,
        "win_rate": res.win_rate,
        "mdd": res.mdd,
        "sharpe_ratio": res.sharpe_ratio,
        "num_liquidations": res.num_liquidations,
        "realized_pnl": res.realized_pnl,
    }


def _rank_results(raw: list[dict], weights: tuple[float, float, float]) -> list[RankedResult]:
    """스코어 계산 후 내림차순 정렬, RankedResult 리스트 반환."""
    w_sharpe, w_return, w_mdd = weights

    out_sharpes = [r["out"]["sharpe_ratio"] for r in raw]
    out_returns = [r["out"]["total_return_pct"] for r in raw]
    out_mdds = [r["out"]["mdd"] for r in raw]  # 음수 값 (예: -20.0%)
    # abs(mdd)로 z-정규화: 절댓값이 클수록 나쁜 MDD → z 높음 → 스코어에서 차감 커짐
    out_mdds_abs = [abs(v) for v in out_mdds]

    def z_normalize(values: list[float]) -> list[float]:
        arr = np.array(values, dtype=float)
        if len(arr) < 2:
            return [0.0] * len(values)
        std = float(np.std(arr))
        if std < 1e-12 or math.isnan(std):
            return [0.0] * len(values)
        mean = float(np.mean(arr))
        return [(v - mean) / std for v in values]

    z_sharpes = z_normalize(out_sharpes)
    z_returns = z_normalize(out_returns)
    z_mdds = z_normalize(out_mdds_abs)  # abs(mdd) z: 클수록 나쁨 → 스코어에서 차감

    scored: list[tuple[float, int]] = []
    for idx, r in enumerate(raw):
        score = (
            w_sharpe * z_sharpes[idx]
            + w_return * z_returns[idx]
            - w_mdd * z_mdds[idx]   # 큰 절댓값 MDD → z 높음 → 차감 커짐 → 페널티
        )
        scored.append((score, idx))

    # 스코어 내림차순
    scored.sort(key=lambda x: x[0], reverse=True)

    ranked: list[RankedResult] = []
    for rank, (score, idx) in enumerate(scored, start=1):
        r = raw[idx]
        ranked.append(
            RankedResult(
                rank=rank,
                symbol=r["symbol"],
                strategy_name=r["strategy_name"],
                params=r["params"],
                leverage=r["leverage"],
                score=score,
                in_sample_metrics=r["in"],
                out_sample_metrics=r["out"],
                warnings=r["warnings"],
            )
        )

    return ranked


def _build_recommended_config(ranked: list[RankedResult]) -> dict:
    """경고 없는 최상위 결과로 recommended_config 구성. 모두 경고 있으면 최상위 사용."""
    best = next((r for r in ranked if not r.warnings), None)
    if best is None:
        best = ranked[0]

    return {
        "strategy": best.strategy_name,
        "strategy_params": best.params,
        "leverage": {"default": best.leverage, "max": best.leverage * 2},
        "symbol": best.symbol,
        "expected_out_sample": {
            "sharpe_ratio": best.out_sample_metrics.get("sharpe_ratio", 0.0),
            "total_return_pct": best.out_sample_metrics.get("total_return_pct", 0.0),
            "mdd_pct": best.out_sample_metrics.get("mdd", 0.0),
        },
    }


# ──────────────────────────────────────────────
# CLI 진입점 — 바이낸스 실데이터 그리드서치
# ──────────────────────────────────────────────

def main() -> None:
    import argparse

    from core.config import load_config
    from core.market_data import BinanceFuturesClient

    # 그리드서치 중 잔고 소진 조합의 진입 거부 경고는 노이즈이므로 억제
    logging.getLogger("core.paper_wallet").setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description="바이낸스 USDT-M 선물 파라미터 최적화")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--days", type=int, default=None, help="과거 데이터 기간(일)")
    parser.add_argument("--interval", default=None, help="캔들 인터벌(기본: config)")
    parser.add_argument("--top", type=int, default=15, help="출력할 상위 결과 수")
    args = parser.parse_args()

    cfg = load_config(args.config)
    opt = cfg.optimize
    param_grid = opt.get("param_grid", {})
    leverage_grid = list(opt.get("leverage_grid", [cfg.leverage_default]))
    split_ratio = float(opt.get("split_ratio", 0.7))
    sw = opt.get("score_weights", {})
    weights = (
        float(sw.get("sharpe", 0.5)),
        float(sw.get("total_return", 0.3)),
        float(sw.get("mdd", 0.2)),
    )
    days = args.days or int(cfg.backtest.get("days", 365))
    interval = args.interval or cfg.candle_interval
    initial_balance = float(cfg.backtest.get("initial_balance_usdt", cfg.initial_balance_usdt))

    client = BinanceFuturesClient(request_interval_sec=cfg.request_interval_sec)

    print(
        f"최적화: 심볼 {cfg.symbols} | {interval} | {days}일 | "
        f"레버리지 {leverage_grid} | split {split_ratio}"
    )
    candles_map: dict[str, Any] = {}
    for symbol in cfg.symbols:
        candles = client.fetch_historical_candles(symbol, days, interval)
        candles_map[symbol] = candles
        print(f"  {symbol}: {len(candles)}봉 수집")

    optimizer = GridSearchOptimizer(
        symbols=cfg.symbols,
        candles_map=candles_map,
        param_grids=param_grid,
        leverage_grid=leverage_grid,
        split_ratio=split_ratio,
        score_weights=weights,
        initial_balance=initial_balance,
    )
    result = optimizer.run()

    print(f"\n=== 랭킹 (총 {result.total_combinations_tested} 조합) ===")
    result.print_ranking_table(args.top)

    print("\n=== 추천 config (인샘플/아웃샘플 검증 기반) ===")
    print(result.to_config_yaml())
    # 추천 config 에 해당하는 랭킹 항목의 경고만 출력 (best 와 다를 수 있음)
    rec = result.recommended_config
    rec_lev = rec.get("leverage", {}).get("default")
    rec_rr = next(
        (
            r for r in result.ranked_results
            if r.strategy_name == rec.get("strategy")
            and r.symbol == rec.get("symbol")
            and r.leverage == rec_lev
            and r.params == rec.get("strategy_params")
        ),
        result.best,
    )
    if rec_rr.warnings:
        print("⚠️  추천 결과 경고:")
        for w in rec_rr.warnings:
            print(f"   - {w}")
    print(
        "\n※ 과거데이터 기준 최적화 결과입니다. 자동매매·레버리지 선물은 "
        "수익을 보장하지 않으며 과최적화/슬리피지/시장변화로 실시간에서 무너질 수 있습니다."
    )


if __name__ == "__main__":
    main()
