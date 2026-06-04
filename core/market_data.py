"""바이낸스 USDT-M 선물 공개 REST API 클라이언트."""
import logging
import time
from typing import Protocol

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE = "https://fapi.binance.com"


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
    """바이낸스 USDT-M 선물 공개 API. 인증 불필요."""

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

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        """지수 백오프 재시도 포함 GET 요청."""
        url = BASE + path
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
                return resp
            except (requests.ConnectionError, requests.Timeout) as exc:
                logger.warning("연결 오류 %s (시도 %d): %s", url, attempt + 1, exc)
                if attempt < self._max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
        raise requests.RequestException(f"최대 재시도 초과: {url}")

    def get_ticker(self, symbols: list[str]) -> dict[str, float]:
        """현재가 조회. {symbol: float} 반환, 실패 시 {}."""
        try:
            if len(symbols) == 1:
                resp = self._get("/fapi/v1/ticker/price", {"symbol": symbols[0]})
                data = resp.json()
                return {data["symbol"]: float(data["price"])}
            else:
                resp = self._get("/fapi/v1/ticker/price")
                all_prices: dict[str, float] = {
                    item["symbol"]: float(item["price"]) for item in resp.json()
                }
                sym_set = set(symbols)
                return {k: v for k, v in all_prices.items() if k in sym_set}
        except Exception as exc:
            logger.error("get_ticker 실패: %s", exc)
            return {}

    def get_ohlcv(
        self,
        symbol: str,
        interval: str = "1h",
        count: int = 200,
        end_time: int | None = None,
    ) -> pd.DataFrame:
        """OHLCV 캔들 DataFrame 반환. 실패 시 빈 DataFrame."""
        params: dict = {"symbol": symbol, "interval": interval, "limit": count}
        if end_time is not None:
            params["endTime"] = end_time
        try:
            resp = self._get("/fapi/v1/klines", params)
            raw = resp.json()
            if not raw:
                return pd.DataFrame()
            return _parse_klines(raw)
        except Exception as exc:
            logger.error("get_ohlcv(%s) 실패: %s", symbol, exc)
            return pd.DataFrame()

    def get_mark_price(self, symbols: list[str]) -> dict[str, float]:
        """마크 가격 조회. 실패 시 get_ticker 폴백."""
        try:
            if len(symbols) == 1:
                resp = self._get("/fapi/v1/premiumIndex", {"symbol": symbols[0]})
                data = resp.json()
                return {data["symbol"]: float(data["markPrice"])}
            else:
                resp = self._get("/fapi/v1/premiumIndex")
                all_marks: dict[str, float] = {
                    item["symbol"]: float(item["markPrice"]) for item in resp.json()
                }
                sym_set = set(symbols)
                return {k: v for k, v in all_marks.items() if k in sym_set}
        except Exception as exc:
            logger.warning("get_mark_price 실패, get_ticker 폴백: %s", exc)
            return self.get_ticker(symbols)

    def fetch_historical_candles(
        self,
        symbol: str,
        days: int,
        interval: str = "1h",
    ) -> pd.DataFrame:
        """days 분량의 과거 캔들을 endTime 페이지네이션으로 수집."""
        limit = 1500
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - days * 86_400_000

        frames: list[pd.DataFrame] = []
        end_time: int | None = None

        while True:
            params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
            if end_time is not None:
                params["endTime"] = end_time
            try:
                resp = self._get("/fapi/v1/klines", params)
                raw = resp.json()
            except Exception as exc:
                logger.error("fetch_historical_candles(%s) 실패: %s", symbol, exc)
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

            # 다음 페이지: 가장 오래된 openTime - 1
            end_time = oldest_ms - 1

        if not frames:
            return pd.DataFrame()

        combined = pd.concat(frames)
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)

        # cutoff 이후 데이터만 유지
        cutoff_dt = pd.Timestamp(cutoff_ms, unit="ms", tz="UTC")
        combined = combined[combined.index >= cutoff_dt]

        return combined


def _parse_klines(raw: list) -> pd.DataFrame:
    """klines 응답 배열 → DataFrame[open,high,low,close,volume] + DatetimeIndex."""
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
