"""바이낸스 시세 공개 REST 클라이언트 (멀티소스 폴백).

소스 우선순위:
 1. fapi.binance.com (USDT-M 선물) — 한국/로컬에서 동작.
 2. data-api.binance.vision (공개 데이터 도메인, 현물) — 일부 지역(예: GitHub Actions 미국 IP)에서
    fapi 가 451(지역 차단)일 때 폴백. 심볼/인터벌/캔들 포맷이 fapi 와 동일.
페이퍼 트레이딩이므로 현물 가격으로도 시뮬레이션에 충분하다(선물 베이시스는 무시 가능).
"""
import logging
import time
from typing import Protocol

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# (이름, base, kline 경로, ticker 경로, mark 경로 or None)
_SOURCES = [
    {
        "name": "binance-futures",
        "base": "https://fapi.binance.com",
        "kline": "/fapi/v1/klines",
        "ticker": "/fapi/v1/ticker/price",
        "mark": "/fapi/v1/premiumIndex",
    },
    {
        "name": "binance-vision",
        "base": "https://data-api.binance.vision",
        "kline": "/api/v3/klines",
        "ticker": "/api/v3/ticker/price",
        "mark": None,
    },
]


class MarketDataSource(Protocol):
    def get_ticker(self, symbols: list[str]) -> dict[str, float]: ...
    def get_ohlcv(
        self,
        symbol: str,
        interval: str = "1h",
        count: int = 200,
        end_time: int | None = None,
    ) -> pd.DataFrame: ...


class BinanceFuturesClient:
    """바이낸스 공개 시세 API (선물 우선, vision 폴백). 인증 불필요."""

    def __init__(
        self,
        request_interval_sec: float = 0.2,
        max_retries: int = 3,
    ) -> None:
        self._interval = request_interval_sec
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def _throttle(self) -> None:
        time.sleep(self._interval)

    def _request(self, base: str, path: str, params: dict | None = None):
        """단일 소스 GET (429/네트워크 재시도). 451 등 영구 오류는 즉시 예외 → 상위에서 다음 소스로."""
        url = base + path
        delay = 1.0
        for attempt in range(self._max_retries):
            try:
                self._throttle()
                resp = self._session.get(url, params=params, timeout=10)
                if resp.status_code in (429, 418):
                    logger.warning("레이트리밋 %s (시도 %d)", resp.status_code, attempt + 1)
                    time.sleep(delay)
                    delay *= 2
                    continue
                resp.raise_for_status()
                return resp.json()
            except (requests.ConnectionError, requests.Timeout) as exc:
                logger.warning("연결 오류 %s (시도 %d): %s", url, attempt + 1, exc)
                if attempt < self._max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
        raise requests.RequestException(f"최대 재시도 초과: {url}")

    def get_ticker(self, symbols: list[str]) -> dict[str, float]:
        """현재가 조회. {symbol: float} 반환. 소스 순회, 첫 성공 반환. 모두 실패 시 {}."""
        sym_set = set(symbols)
        for src in _SOURCES:
            try:
                if len(symbols) == 1:
                    data = self._request(src["base"], src["ticker"], {"symbol": symbols[0]})
                    return {data["symbol"]: float(data["price"])}
                data = self._request(src["base"], src["ticker"])
                result = {
                    item["symbol"]: float(item["price"])
                    for item in data
                    if item["symbol"] in sym_set
                }
                if result:
                    return result
            except Exception as exc:
                logger.warning("get_ticker[%s] 실패, 다음 소스: %s", src["name"], exc)
        logger.error("get_ticker 모든 소스 실패")
        return {}

    def get_ohlcv(
        self,
        symbol: str,
        interval: str = "1h",
        count: int = 200,
        end_time: int | None = None,
    ) -> pd.DataFrame:
        """OHLCV 캔들 DataFrame. 소스 순회, 첫 성공 반환. 실패 시 빈 DataFrame."""
        params: dict = {"symbol": symbol, "interval": interval, "limit": count}
        if end_time is not None:
            params["endTime"] = end_time
        for src in _SOURCES:
            try:
                raw = self._request(src["base"], src["kline"], params)
                if raw:
                    return _parse_klines(raw)
            except Exception as exc:
                logger.warning("get_ohlcv[%s](%s) 실패, 다음 소스: %s", src["name"], symbol, exc)
        logger.error("get_ohlcv(%s) 모든 소스 실패", symbol)
        return pd.DataFrame()

    def get_mark_price(self, symbols: list[str]) -> dict[str, float]:
        """마크 가격(선물 소스). 없거나 실패 시 get_ticker(멀티소스) 폴백."""
        sym_set = set(symbols)
        for src in _SOURCES:
            if not src["mark"]:
                continue
            try:
                if len(symbols) == 1:
                    data = self._request(src["base"], src["mark"], {"symbol": symbols[0]})
                    return {data["symbol"]: float(data["markPrice"])}
                data = self._request(src["base"], src["mark"])
                marks = {
                    item["symbol"]: float(item["markPrice"])
                    for item in data
                    if item["symbol"] in sym_set
                }
                if marks:
                    return marks
            except Exception as exc:
                logger.warning("get_mark_price[%s] 실패, 폴백: %s", src["name"], exc)
        return self.get_ticker(symbols)

    def fetch_historical_candles(
        self,
        symbol: str,
        days: int,
        interval: str = "1h",
    ) -> pd.DataFrame:
        """days 분량 과거 캔들을 endTime 페이지네이션으로 수집. 소스 순회."""
        for src in _SOURCES:
            df = self._fetch_historical_from(src, symbol, days, interval)
            if not df.empty:
                return df
        logger.error("fetch_historical_candles(%s) 모든 소스 실패", symbol)
        return pd.DataFrame()

    def _fetch_historical_from(
        self, src: dict, symbol: str, days: int, interval: str
    ) -> pd.DataFrame:
        limit = 1000  # 선물 1500/현물 1000 모두 안전
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - days * 86_400_000

        frames: list[pd.DataFrame] = []
        end_time: int | None = None

        while True:
            params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
            if end_time is not None:
                params["endTime"] = end_time
            try:
                raw = self._request(src["base"], src["kline"], params)
            except Exception as exc:
                logger.warning(
                    "fetch_historical[%s](%s) 실패: %s", src["name"], symbol, exc
                )
                break
            if not raw:
                break
            df = _parse_klines(raw)
            if df.empty:
                break
            frames.append(df)
            oldest_ms = int(df.index[0].timestamp() * 1000)
            if oldest_ms <= cutoff_ms:
                break
            end_time = oldest_ms - 1

        if not frames:
            return pd.DataFrame()

        combined = pd.concat(frames)
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)
        cutoff_dt = pd.Timestamp(cutoff_ms, unit="ms", tz="UTC")
        combined = combined[combined.index >= cutoff_dt]
        return combined


def _parse_klines(raw: list) -> pd.DataFrame:
    """klines 응답 배열 → DataFrame[open,high,low,close,volume] + DatetimeIndex.
    (fapi 선물 / api/v3 현물 / vision 모두 동일 배열 포맷)"""
    records = [
        {
            "open_time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in raw
    ]
    df = pd.DataFrame(records)
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.index.name = None
    df = df[["open", "high", "low", "close", "volume"]]
    df.sort_index(inplace=True)
    return df
