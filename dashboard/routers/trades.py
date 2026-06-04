"""GET /api/trades — 최근 거래 목록."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/trades")
async def get_trades(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    symbol: Optional[str] = Query(default=None),
) -> JSONResponse:
    reader = request.app.state.reader
    data = reader.get_trades(limit=limit, offset=offset, symbol=symbol)
    return JSONResponse(content=data)
