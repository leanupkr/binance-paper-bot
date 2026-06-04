"""core/config.py — AppConfig 로더 (바이낸스 USDT-M 선물 페이퍼 트레이딩)"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv


# ──────────────────────────────────────────────────────────────
# 서브 설정 데이터클래스
# ──────────────────────────────────────────────────────────────

@dataclass
class RiskConfig:
    position_size_pct: float        # 1회 진입 증거금 = 가용잔고의 % (예: 20.0)
    stop_loss_pct: float            # 손절선 % (예: 3.0)
    take_profit_pct: float          # 익절선 % (0 = 비활성)
    max_daily_loss_pct: float       # 일일 최대 손실한도 %
    max_position_pct_per_symbol: float  # 심볼당 최대 명목 비중 %
    allow_long: bool
    allow_short: bool


@dataclass
class SlackConfig:
    enabled: bool
    bot_token: str
    channel_id: str
    events: dict[str, bool]
    retry_max: int
    retry_delay_sec: float


# ──────────────────────────────────────────────────────────────
# 최상위 AppConfig
# ──────────────────────────────────────────────────────────────

@dataclass
class AppConfig:
    mode: str
    exchange: str
    symbols: list[str]

    # market_data.*
    poll_interval_sec: int
    candle_interval: str
    candle_count: int
    request_interval_sec: float
    max_consecutive_failures: int

    # account.*
    initial_balance_usdt: float
    taker_fee_rate: float
    maker_fee_rate: float
    slippage_bp: float
    min_notional_usdt: float
    margin_mode: str
    maintenance_margin_rate: float
    funding_enabled: bool

    # leverage.*
    leverage_default: int
    leverage_max: int

    # 전략
    strategy: str
    strategy_params: dict

    # 서브 설정
    risk: RiskConfig
    slack: SlackConfig

    # 저장소 / 대시보드
    db_path: str
    dashboard_host: str
    dashboard_port: int
    dashboard_poll_ms: int

    # 백테스트 / 최적화 (dict 그대로 전달)
    backtest: dict
    optimize: dict


# ──────────────────────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────────────────────

def _assert_paper_mode(mode: str) -> None:
    """live 모드 진입 차단 — 실거래 사고 방지"""
    if mode == "live":
        raise RuntimeError(
            "[안전장치] live 모드는 미구현입니다. 실거래 사고 방지를 위해 종료합니다. "
            "config.yaml 의 mode 를 'paper' 로 두세요."
        )


def _build_risk(raw: dict) -> RiskConfig:
    return RiskConfig(
        position_size_pct=float(raw["position_size_pct"]),
        stop_loss_pct=float(raw["stop_loss_pct"]),
        take_profit_pct=float(raw["take_profit_pct"]),
        max_daily_loss_pct=float(raw["max_daily_loss_pct"]),
        max_position_pct_per_symbol=float(raw["max_position_pct_per_symbol"]),
        allow_long=bool(raw.get("allow_long", True)),
        allow_short=bool(raw.get("allow_short", True)),
    )


def _build_slack(raw: dict, bot_token: str, channel_id: str) -> SlackConfig:
    """enabled 이 true 라도 토큰/채널 중 하나라도 비면 enabled=False 강등"""
    enabled = bool(raw.get("enabled", False))
    if enabled and (not bot_token or not channel_id):
        enabled = False

    return SlackConfig(
        enabled=enabled,
        bot_token=bot_token,
        channel_id=channel_id,
        events=dict(raw.get("events", {})),
        retry_max=int(raw.get("retry_max", 3)),
        retry_delay_sec=float(raw.get("retry_delay_sec", 5.0)),
    )


# ──────────────────────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> AppConfig:
    """config.yaml + .env 로드 후 AppConfig 반환. live 모드 시 RuntimeError."""
    load_dotenv()  # .env → 환경변수 병합 (이미 설정된 값 덮어쓰지 않음)

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    mode: str = raw.get("mode", "paper")
    _assert_paper_mode(mode)

    md = raw.get("market_data", {})
    acct = raw.get("account", {})
    lev = raw.get("leverage", {})
    dash = raw.get("dashboard", {})
    storage = raw.get("storage", {})

    # .env 에서 슬랙 시크릿 로드
    bot_token: str = os.environ.get("SLACK_BOT_TOKEN", "")
    channel_id: str = os.environ.get("SLACK_CHANNEL_ID", "")

    return AppConfig(
        mode=mode,
        exchange=raw.get("exchange", "binance_futures"),
        symbols=list(raw.get("symbols", [])),

        # market_data
        poll_interval_sec=int(md.get("poll_interval_sec", 10)),
        candle_interval=str(md.get("candle_interval", "1h")),
        candle_count=int(md.get("candle_count", 200)),
        request_interval_sec=float(md.get("request_interval_sec", 0.2)),
        max_consecutive_failures=int(md.get("max_consecutive_failures", 5)),

        # account
        initial_balance_usdt=float(acct.get("initial_balance_usdt", 1000.0)),
        taker_fee_rate=float(acct.get("taker_fee_rate", 0.0005)),
        maker_fee_rate=float(acct.get("maker_fee_rate", 0.0002)),
        slippage_bp=float(acct.get("slippage_bp", 0.0)),
        min_notional_usdt=float(acct.get("min_notional_usdt", 5.0)),
        margin_mode=str(acct.get("margin_mode", "isolated")),
        maintenance_margin_rate=float(acct.get("maintenance_margin_rate", 0.005)),
        funding_enabled=bool(acct.get("funding_enabled", False)),

        # leverage
        leverage_default=int(lev.get("default", 3)),
        leverage_max=int(lev.get("max", 10)),

        # 전략
        strategy=str(raw.get("strategy", "")),
        strategy_params=dict(raw.get("strategy_params", {})),

        # 서브 설정
        risk=_build_risk(raw.get("risk", {})),
        slack=_build_slack(raw.get("slack", {}), bot_token, channel_id),

        # 저장소 / 대시보드
        db_path=str(storage.get("db_path", "data/paper_bot.db")),
        dashboard_host=str(dash.get("host", "127.0.0.1")),
        dashboard_port=int(dash.get("port", 8000)),
        dashboard_poll_ms=int(dash.get("poll_interval_ms", 5000)),

        # dict 그대로
        backtest=dict(raw.get("backtest", {})),
        optimize=dict(raw.get("optimize", {})),
    )
