"""GET /api/report — 성과 지표 리포트."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/report")
async def get_report(request: Request) -> JSONResponse:
    reader = request.app.state.reader
    data = reader.get_performance_report()
    return JSONResponse(content=data)
