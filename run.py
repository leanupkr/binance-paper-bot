"""
run.py — 페이퍼 트레이딩 봇 진입점.
사용법: python3 run.py [--config config.yaml]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(description="바이낸스 USDT-M 선물 페이퍼 트레이딩 봇")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="설정 파일 경로 (기본: config.yaml)",
    )
    args = parser.parse_args()

    from core.config import load_config
    config = load_config(args.config)

    # DB 디렉토리 자동 생성
    db_dir = os.path.dirname(config.db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    print("=" * 60)
    print("  바이낸스 USDT-M 선물 페이퍼 트레이딩 봇")
    print("=" * 60)
    print(f"  모드      : {config.mode}")
    print(f"  심볼      : {', '.join(config.symbols)}")
    print(f"  전략      : {config.strategy}")
    print(f"  레버리지  : {config.leverage_default}x (최대 {config.leverage_max}x)")
    print(f"  초기잔고  : {config.initial_balance_usdt:.2f} USDT")
    print(f"  캔들 주기 : {config.candle_interval}")
    print(f"  슬랙 알림 : {'활성' if config.slack.enabled else '비활성'}")
    print("=" * 60)

    from core.orchestrator import Orchestrator
    bot = Orchestrator(config)
    bot.run()


if __name__ == "__main__":
    main()
