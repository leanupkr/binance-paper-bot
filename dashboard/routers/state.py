"""GET /api/state — 계좌 현황 (잔고/포지션/평가자산)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/state")
async def get_state(request: Request) -> JSONResponse:
    reader = request.app.state.reader
    data = reader.get_account_state()
    return JSONResponse(content=data)
