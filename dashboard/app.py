"""
dashboard/app.py — FastAPI 대시보드 앱.
lifespan 으로 config + state_reader 초기화. /api prefix 라우터 + 정적 파일 마운트.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.config import load_config
from dashboard.state_reader import create_state_reader
from dashboard.routers import state, trades, equity, report

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg = load_config("config.yaml")
    app.state.config = cfg
    app.state.reader = create_state_reader(cfg)
    logger.info("대시보드 StateReader 초기화 완료: %s", cfg.db_path)
    yield
    logger.info("대시보드 종료")


app = FastAPI(
    title="Paper Trading Dashboard",
    description="바이낸스 USDT-M 선물 페이퍼 트레이딩 대시보드",
    version="1.0.0",
    lifespan=lifespan,
)

# API 라우터
app.include_router(state.router, prefix="/api")
app.include_router(trades.router, prefix="/api")
app.include_router(equity.router, prefix="/api")
app.include_router(report.router, prefix="/api")

# 정적 파일 (index.html 포함)
_static_dir = __file__.replace("app.py", "static")
app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
