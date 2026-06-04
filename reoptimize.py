"""
주기적 자동 재최적화.

최근 데이터로 (전략 × 파라미터) 그리드서치를 돌려 인/아웃샘플 검증을 통과한
'경고 없는' 최적 조합을 찾아 config.yaml 의 strategy / strategy_params 를 갱신한다.

안전장치 (정직성):
  - 경고(과최적화/거래수 부족/청산 과다)가 있는 후보는 적용하지 않는다.
  - 레버리지는 자동 상향하지 않는다(현 config 의 leverage.default 고정). 과최적화 신호로
    레버리지를 키우면 청산·파산 위험만 커지기 때문.
  - 변경이 없으면 config.yaml 을 건드리지 않는다.

이 스크립트는 수익을 보장하지 않는다. '최근 시장에 맞는 합리적 파라미터 유지'가 목적이다.
"""
from __future__ import annotations

import logging

from ruamel.yaml import YAML

from core.config import load_config
from core.market_data import BinanceFuturesClient
from optimize import GridSearchOptimizer
import strategies  # noqa: F401  레지스트리 등록

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("core.market_data").setLevel(logging.ERROR)
logging.getLogger("core.paper_wallet").setLevel(logging.ERROR)
log = logging.getLogger("reoptimize")


def main() -> None:
    cfg = load_config()
    opt = cfg.optimize
    days = int(cfg.backtest.get("days", 365))
    interval = cfg.candle_interval

    client = BinanceFuturesClient(request_interval_sec=cfg.request_interval_sec)
    candles_map = {}
    for sym in cfg.symbols:
        df = client.fetch_historical_candles(sym, days, interval)
        if not df.empty:
            candles_map[sym] = df
        log.info("%s: %d 봉", sym, len(df))

    if not candles_map:
        log.error("데이터 수집 실패 — 재최적화 중단(현 config 유지)")
        return

    # 안전: 전략은 현재 선택된 것으로 고정하고 그 파라미터만 튜닝(주간마다 전략이
    #       뒤바뀌는 과최적화 방지). 현 전략 그리드가 없으면 변경하지 않음.
    full_grid = opt.get("param_grid", {})
    active_grid = {cfg.strategy: full_grid[cfg.strategy]} if cfg.strategy in full_grid else {}
    if not active_grid:
        log.warning("현 전략(%s) 그리드 없음 — 재최적화 생략(현 설정 유지)", cfg.strategy)
        return

    sw = opt.get("score_weights", {})
    optimizer = GridSearchOptimizer(
        symbols=list(candles_map.keys()),
        candles_map=candles_map,
        param_grids=active_grid,
        # 안전: 레버리지는 현 default 로 고정(자동 상향 금지)
        leverage_grid=[cfg.leverage_default],
        split_ratio=float(opt.get("split_ratio", 0.7)),
        score_weights=(
            float(sw.get("sharpe", 0.5)),
            float(sw.get("total_return", 0.3)),
            float(sw.get("mdd", 0.2)),
        ),
        initial_balance=float(cfg.backtest.get("initial_balance_usdt", cfg.initial_balance_usdt)),
    )
    result = optimizer.run()

    # 경고 없는 최상위만 채택
    best = next((r for r in result.ranked_results if not r.warnings), None)
    if best is None:
        log.warning("경고 없는 후보가 없음 — config 변경하지 않음(현 설정 유지)")
        return

    new_strategy = best.strategy_name
    new_params = {k: (int(v) if isinstance(v, bool) is False and float(v).is_integer() else v)
                  for k, v in best.params.items()}
    m = best.out_sample_metrics
    log.info(
        "추천: %s %s (out-sample sharpe=%.2f ret=%.1f%% mdd=%.1f%% trades=%d)",
        new_strategy, new_params,
        m.get("sharpe_ratio", 0.0), m.get("total_return_pct", 0.0),
        m.get("mdd", 0.0), m.get("num_trades", 0),
    )

    yaml = YAML()
    yaml.preserve_quotes = True
    with open("config.yaml", "r", encoding="utf-8") as f:
        doc = yaml.load(f)

    cur_params = dict(doc.get("strategy_params", {}).get(new_strategy, {}))
    changed = doc.get("strategy") != new_strategy or cur_params != new_params
    if not changed:
        log.info("현 config 와 동일 — 변경 없음")
        return

    doc["strategy"] = new_strategy
    if "strategy_params" not in doc:
        doc["strategy_params"] = {}
    doc["strategy_params"][new_strategy] = new_params

    with open("config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(doc, f)
    log.info("config.yaml 갱신: strategy=%s params=%s (leverage 유지=%dx)",
             new_strategy, new_params, cfg.leverage_default)


if __name__ == "__main__":
    main()
