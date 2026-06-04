"""
tick.py — 단일 틱 실행 진입점 (GitHub Actions/cron 용).

무한루프 없이 _tick() 1회 실행 후 상태를 Supabase(또는 SQLite)에 저장하고 종료.
GitHub Actions 의 schedule 워크플로우가 주기적으로 이 스크립트를 호출한다.

환경변수:
    STORAGE_BACKEND : "sqlite"(기본) | "supabase"
    SUPABASE_DB_URL : Postgres DSN (STORAGE_BACKEND=supabase 시 필수)

사용법:
    python tick.py [--config config.yaml]
"""
from __future__ import annotations

import argparse
import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="단일 틱 실행 (GitHub Actions/cron 용)")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="설정 파일 경로 (기본: config.yaml)",
    )
    args = parser.parse_args()

    from core.config import load_config
    from core.orchestrator import Orchestrator

    # SQLite 백엔드 시 data 디렉토리 자동 생성
    backend = os.environ.get("STORAGE_BACKEND", "sqlite").lower()
    if backend != "supabase":
        pass  # load_config 후 db_path 로 처리

    cfg = load_config(args.config)

    if backend != "supabase":
        db_dir = os.path.dirname(cfg.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    logger.info(
        "단일 틱 시작 | backend=%s mode=%s strategy=%s symbols=%s",
        backend,
        cfg.mode,
        cfg.strategy,
        cfg.symbols,
    )

    # Orchestrator 생성 → run_once() 는 내부에서 storage.close() 까지 처리
    orchestrator = Orchestrator(cfg)
    orchestrator.run_once()


if __name__ == "__main__":
    main()
