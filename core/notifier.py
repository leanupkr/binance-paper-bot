"""core/notifier.py — Slack 알림 (바이낸스 USDT-M 선물 페이퍼 트레이딩)"""
from __future__ import annotations

import dataclasses
import logging
import time
from enum import Enum
from typing import Protocol

import requests

from core.config import SlackConfig

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 이벤트 열거형 — 값은 config slack.events 키와 정확히 일치
# ──────────────────────────────────────────────────────────────────────────────

class NotifyEvent(str, Enum):
    ON_OPEN            = "on_open"
    ON_CLOSE           = "on_close"
    ON_STOP_LOSS       = "on_stop_loss"
    ON_TAKE_PROFIT     = "on_take_profit"
    ON_LIQUIDATION     = "on_liquidation"
    ON_DAILY_LOSS_LIM  = "on_daily_loss_limit"
    ON_BOT_START       = "on_bot_start"
    ON_BOT_STOP        = "on_bot_stop"
    ON_ERROR           = "on_error"
    ON_DAILY_SUMMARY   = "on_daily_summary"


# ──────────────────────────────────────────────────────────────────────────────
# Protocol
# ──────────────────────────────────────────────────────────────────────────────

class Notifier(Protocol):
    def notify(self, event: NotifyEvent, payload: object) -> None: ...
    def is_enabled(self, event: NotifyEvent) -> bool: ...


# ──────────────────────────────────────────────────────────────────────────────
# 내부 헬퍼 — payload 를 dict 로 정규화
# ──────────────────────────────────────────────────────────────────────────────

def _to_dict(payload: object) -> dict:
    """dataclass 또는 dict 를 dict 로 변환. 그 외 타입은 str 로 감쌈."""
    if isinstance(payload, dict):
        return payload
    if dataclasses.is_dataclass(payload) and not isinstance(payload, type):
        return dataclasses.asdict(payload)  # type: ignore[arg-type]
    return {"message": str(payload)}


# ──────────────────────────────────────────────────────────────────────────────
# Block Kit 빌더
# ──────────────────────────────────────────────────────────────────────────────

def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _divider() -> dict:
    return {"type": "divider"}


def _build_blocks(event: NotifyEvent, d: dict) -> list[dict]:
    """이벤트별 Slack Block Kit blocks 반환."""

    if event == NotifyEvent.ON_OPEN:
        side  = d.get("side", "?")
        sym   = d.get("symbol", "?")
        lev   = d.get("leverage", "?")
        ep    = d.get("entry_price", d.get("filled_price", "?"))
        mg    = d.get("margin_usdt", "?")
        lp    = d.get("liquidation_price", "?")
        emoji = "🟢" if str(side).upper() == "LONG" else "🔴"
        text  = (
            f"{emoji} *포지션 진입* | {sym}\n"
            f">방향: `{side}` | 레버리지: `{lev}x`\n"
            f">진입가: `{ep}` USDT | 증거금: `{mg}` USDT\n"
            f">청산가: `{lp}` USDT"
        )
        return [_section(text)]

    if event == NotifyEvent.ON_CLOSE:
        sym    = d.get("symbol", "?")
        side   = d.get("side", "?")
        fp     = d.get("filled_price", "?")
        rpnl   = d.get("realized_pnl", 0.0)
        try:
            pnl_val = float(rpnl)
            pnl_str = f"+{pnl_val:.4f}" if pnl_val >= 0 else f"{pnl_val:.4f}"
            emoji   = "💰" if pnl_val >= 0 else "📉"
        except (TypeError, ValueError):
            pnl_str = str(rpnl)
            emoji   = "📊"
        text = (
            f"{emoji} *포지션 청산* | {sym}\n"
            f">방향: `{side}` | 체결가: `{fp}` USDT\n"
            f">실현손익: `{pnl_str}` USDT"
        )
        return [_section(text)]

    if event == NotifyEvent.ON_STOP_LOSS:
        sym  = d.get("symbol", "?")
        side = d.get("side", "?")
        ep   = d.get("entry_price", "?")
        cp   = d.get("current_price", "?")
        rpnl = d.get("realized_pnl", "?")
        text = (
            f"🛑 *손절(Stop-Loss)* | {sym}\n"
            f">방향: `{side}` | 진입가: `{ep}` → 현재가: `{cp}` USDT\n"
            f">실현손익: `{rpnl}` USDT"
        )
        return [_section(text)]

    if event == NotifyEvent.ON_TAKE_PROFIT:
        sym  = d.get("symbol", "?")
        side = d.get("side", "?")
        ep   = d.get("entry_price", "?")
        cp   = d.get("current_price", "?")
        rpnl = d.get("realized_pnl", "?")
        text = (
            f"🎯 *익절(Take-Profit)* | {sym}\n"
            f">방향: `{side}` | 진입가: `{ep}` → 현재가: `{cp}` USDT\n"
            f">실현손익: `{rpnl}` USDT"
        )
        return [_section(text)]

    if event == NotifyEvent.ON_LIQUIDATION:
        sym  = d.get("symbol", "?")
        side = d.get("side", "?")
        lp   = d.get("liquidation_price", d.get("filled_price", "?"))
        rpnl = d.get("realized_pnl", "?")
        text = (
            f"💥 *강제청산(Liquidation)* | {sym}\n"
            f">방향: `{side}` | 청산가: `{lp}` USDT\n"
            f">실현손익: `{rpnl}` USDT"
        )
        return [_section(text)]

    if event == NotifyEvent.ON_DAILY_LOSS_LIM:
        eq_start = d.get("today_start_equity", "?")
        eq_now   = d.get("current_equity", "?")
        limit    = d.get("max_daily_loss_pct", "?")
        text = (
            f"⚠️ *일일 손실한도 도달*\n"
            f">한도: `{limit}%` | 시작자산: `{eq_start}` → 현재: `{eq_now}` USDT\n"
            f">오늘 신규 진입이 중단됩니다."
        )
        return [_section(text)]

    if event == NotifyEvent.ON_BOT_START:
        balance = d.get("initial_balance_usdt", d.get("balance_usdt", "?"))
        mode    = d.get("mode", "paper")
        strat   = d.get("strategy", "?")
        text = (
            f"🤖 *봇 시작*\n"
            f">모드: `{mode}` | 전략: `{strat}`\n"
            f">초기잔고: `{balance}` USDT"
        )
        return [_section(text)]

    if event == NotifyEvent.ON_BOT_STOP:
        eq     = d.get("equity", "?")
        rpnl   = d.get("realized_pnl", "?")
        trades = d.get("trade_count", "?")
        text = (
            f"🔴 *봇 종료*\n"
            f">최종자산: `{eq}` USDT | 실현손익: `{rpnl}` USDT\n"
            f">총 거래수: `{trades}`"
        )
        return [_section(text)]

    if event == NotifyEvent.ON_ERROR:
        msg = d.get("message", d.get("error", str(d)))
        ctx = d.get("context", "")
        text = f"🚨 *에러 발생*\n>`{msg}`"
        if ctx:
            text += f"\n>컨텍스트: {ctx}"
        return [_section(text)]

    if event == NotifyEvent.ON_DAILY_SUMMARY:
        eq      = d.get("equity", "?")
        rpnl    = d.get("realized_pnl", "?")
        trades  = d.get("trade_count", "?")
        win_r   = d.get("win_rate_pct", "?")
        mdd     = d.get("mdd_pct", "?")
        text = (
            f"📊 *일일 요약*\n"
            f">자산: `{eq}` USDT | 실현손익: `{rpnl}` USDT\n"
            f">거래수: `{trades}` | 승률: `{win_r}%` | MDD: `{mdd}%`"
        )
        return [_section(text)]

    # 기타 — 기본 포맷
    text = f"ℹ️ *{event.value}*\n" + "\n".join(f">{k}: `{v}`" for k, v in d.items())
    return [_section(text)]


# ──────────────────────────────────────────────────────────────────────────────
# SlackNotifier
# ──────────────────────────────────────────────────────────────────────────────

class SlackNotifier:
    """Block Kit 기반 Slack 알림. 예외를 절대 전파하지 않는다."""

    _API_URL = "https://slack.com/api/chat.postMessage"

    def __init__(self, config: SlackConfig) -> None:
        self._config = config

    # ── Public ────────────────────────────────────────────────────────────────

    def is_enabled(self, event: NotifyEvent) -> bool:
        return (
            self._config.enabled
            and self._config.events.get(event.value, False)
        )

    def notify(self, event: NotifyEvent, payload: object) -> None:
        if not self.is_enabled(event):
            return
        d = _to_dict(payload)
        blocks = _build_blocks(event, d)
        self._send(blocks)

    # ── Private ───────────────────────────────────────────────────────────────

    def _send(self, blocks: list[dict]) -> None:
        """Slack API 호출. 실패해도 logging.error 만 남기고 예외 전파 금지."""
        cfg = self._config
        body = {
            "channel": cfg.channel_id,
            "blocks": blocks,
        }
        headers = {
            "Authorization": f"Bearer {cfg.bot_token}",
            "Content-Type": "application/json",
        }

        delay = cfg.retry_delay_sec
        for attempt in range(cfg.retry_max + 1):
            try:
                resp = requests.post(
                    self._API_URL,
                    json=body,
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", delay))
                    logger.warning("Slack 429: Retry-After %.1fs", retry_after)
                    time.sleep(retry_after)
                    continue

                if resp.status_code >= 400:
                    logger.error(
                        "Slack HTTP %d (attempt %d/%d)",
                        resp.status_code, attempt + 1, cfg.retry_max + 1,
                    )
                    if attempt < cfg.retry_max:
                        time.sleep(delay)
                        delay *= 2
                    continue

                data = resp.json()
                if not data.get("ok"):
                    logger.error("Slack API error: %s", data.get("error", "unknown"))
                return

            except (requests.ConnectionError, requests.Timeout) as exc:
                logger.error(
                    "Slack 전송 실패 (attempt %d/%d): %s",
                    attempt + 1, cfg.retry_max + 1, exc,
                )
                if attempt < cfg.retry_max:
                    time.sleep(delay)
                    delay *= 2

        logger.error("Slack 전송 최종 실패 — 메시지 폐기")


# ──────────────────────────────────────────────────────────────────────────────
# NullNotifier
# ──────────────────────────────────────────────────────────────────────────────

class NullNotifier:
    """알림 비활성화용 — 모든 호출 무시."""

    def is_enabled(self, event: NotifyEvent) -> bool:  # noqa: ARG002
        return False

    def notify(self, event: NotifyEvent, payload: object) -> None:  # noqa: ARG002
        pass


# ──────────────────────────────────────────────────────────────────────────────
# 팩토리
# ──────────────────────────────────────────────────────────────────────────────

def create_notifier(config: SlackConfig) -> SlackNotifier | NullNotifier:
    """enabled 이고 토큰/채널이 모두 있으면 SlackNotifier, 아니면 NullNotifier."""
    if config.enabled and config.bot_token and config.channel_id:
        return SlackNotifier(config)
    return NullNotifier()
