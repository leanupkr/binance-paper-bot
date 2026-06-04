"""GET /api/equity — 자산 곡선 (since_hours 기간)."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/equity")
async def get_equity(
    request: Request,
    hours: float = Query(default=24.0, ge=0.1, le=8760.0),
) -> JSONResponse:
    reader = request.app.state.reader
    data = reader.get_equity_curve(since_hours=hours)
    return JSONResponse(content=data)
