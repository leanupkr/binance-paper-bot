"""tests/test_notifier.py — core/notifier.py 단위 테스트 (네트워크 금지)"""
from __future__ import annotations

import pytest
import requests

from core.config import SlackConfig
from core.notifier import (
    NotifyEvent,
    NullNotifier,
    SlackNotifier,
    create_notifier,
)


# ──────────────────────────────────────────────────────────────────────────────
# 픽스처
# ──────────────────────────────────────────────────────────────────────────────

def _make_slack_config(
    enabled: bool = True,
    bot_token: str = "xoxb-test-token",
    channel_id: str = "C12345678",
    events: dict | None = None,
) -> SlackConfig:
    if events is None:
        events = {e.value: True for e in NotifyEvent}
    return SlackConfig(
        enabled=enabled,
        bot_token=bot_token,
        channel_id=channel_id,
        events=events,
        retry_max=2,
        retry_delay_sec=0.0,  # 테스트에서 sleep 없음
    )


# ──────────────────────────────────────────────────────────────────────────────
# NullNotifier
# ──────────────────────────────────────────────────────────────────────────────

class TestNullNotifier:
    def test_is_enabled_always_false(self) -> None:
        n = NullNotifier()
        for event in NotifyEvent:
            assert n.is_enabled(event) is False

    def test_notify_does_not_raise(self) -> None:
        n = NullNotifier()
        # 다양한 payload 타입에서도 예외 없음
        n.notify(NotifyEvent.ON_OPEN, {"symbol": "BTCUSDT"})
        n.notify(NotifyEvent.ON_ERROR, "some error string")
        n.notify(NotifyEvent.ON_BOT_START, None)

    def test_notify_returns_none(self) -> None:
        n = NullNotifier()
        result = n.notify(NotifyEvent.ON_CLOSE, {})
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# create_notifier 팩토리
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateNotifier:
    def test_returns_null_when_disabled(self) -> None:
        cfg = _make_slack_config(enabled=False)
        assert isinstance(create_notifier(cfg), NullNotifier)

    def test_returns_null_when_no_token(self) -> None:
        cfg = _make_slack_config(enabled=True, bot_token="", channel_id="C123")
        assert isinstance(create_notifier(cfg), NullNotifier)

    def test_returns_null_when_no_channel(self) -> None:
        cfg = _make_slack_config(enabled=True, bot_token="xoxb-token", channel_id="")
        assert isinstance(create_notifier(cfg), NullNotifier)

    def test_returns_slack_when_fully_configured(self) -> None:
        cfg = _make_slack_config()
        assert isinstance(create_notifier(cfg), SlackNotifier)

    def test_returns_null_when_both_missing(self) -> None:
        cfg = _make_slack_config(enabled=True, bot_token="", channel_id="")
        assert isinstance(create_notifier(cfg), NullNotifier)


# ──────────────────────────────────────────────────────────────────────────────
# is_enabled 토글
# ──────────────────────────────────────────────────────────────────────────────

class TestIsEnabled:
    def test_all_events_on(self) -> None:
        cfg = _make_slack_config()
        n = SlackNotifier(cfg)
        for event in NotifyEvent:
            assert n.is_enabled(event) is True

    def test_specific_event_off(self) -> None:
        events = {e.value: True for e in NotifyEvent}
        events[NotifyEvent.ON_OPEN.value] = False
        cfg = _make_slack_config(events=events)
        n = SlackNotifier(cfg)
        assert n.is_enabled(NotifyEvent.ON_OPEN) is False
        assert n.is_enabled(NotifyEvent.ON_CLOSE) is True

    def test_globally_disabled(self) -> None:
        cfg = _make_slack_config(enabled=False)
        n = SlackNotifier(cfg)
        for event in NotifyEvent:
            assert n.is_enabled(event) is False

    def test_event_not_in_dict_defaults_false(self) -> None:
        cfg = _make_slack_config(events={})
        n = SlackNotifier(cfg)
        assert n.is_enabled(NotifyEvent.ON_OPEN) is False


# ──────────────────────────────────────────────────────────────────────────────
# SlackNotifier.notify — monkeypatch 기반 (실제 네트워크 금지)
# ──────────────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int = 200, ok: bool = True) -> None:
        self.status_code = status_code
        self.headers: dict = {}
        self._ok = ok

    def json(self) -> dict:
        return {"ok": self._ok}


class TestSlackNotifierSend:
    def test_success_calls_post_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list = []

        def mock_post(*args, **kwargs):
            calls.append((args, kwargs))
            return _FakeResponse(200, ok=True)

        monkeypatch.setattr(requests, "post", mock_post)
        cfg = _make_slack_config()
        n = SlackNotifier(cfg)
        n.notify(NotifyEvent.ON_OPEN, {"symbol": "BTCUSDT", "side": "LONG", "leverage": 3, "filled_price": 60000.0, "margin_usdt": 100.0, "liquidation_price": 45000.0})
        assert len(calls) == 1

    def test_failure_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 500 응답이 와도 예외가 전파되지 않아야 한다."""
        def mock_post(*args, **kwargs):
            return _FakeResponse(500, ok=False)

        monkeypatch.setattr(requests, "post", mock_post)
        cfg = _make_slack_config()
        n = SlackNotifier(cfg)
        # 예외 없이 완료되어야 함
        n.notify(NotifyEvent.ON_ERROR, {"message": "test error"})

    def test_connection_error_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ConnectionError 도 예외 전파 금지."""
        def mock_post(*args, **kwargs):
            raise requests.ConnectionError("network down")

        monkeypatch.setattr(requests, "post", mock_post)
        cfg = _make_slack_config()
        n = SlackNotifier(cfg)
        n.notify(NotifyEvent.ON_BOT_START, {"mode": "paper", "strategy": "test", "initial_balance_usdt": 1000.0})

    def test_retries_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 500 시 retry_max + 1 번 호출된다."""
        calls: list = []

        def mock_post(*args, **kwargs):
            calls.append(1)
            return _FakeResponse(500, ok=False)

        monkeypatch.setattr(requests, "post", mock_post)
        cfg = _make_slack_config()  # retry_max=2
        n = SlackNotifier(cfg)
        n.notify(NotifyEvent.ON_ERROR, {"message": "err"})
        # retry_max=2 이므로 최대 3번 시도
        assert len(calls) == 3

    def test_notify_skipped_when_event_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """is_enabled=False 이면 requests.post 가 호출되지 않는다."""
        calls: list = []

        def mock_post(*args, **kwargs):
            calls.append(1)
            return _FakeResponse(200, ok=True)

        monkeypatch.setattr(requests, "post", mock_post)
        events = {e.value: False for e in NotifyEvent}
        cfg = _make_slack_config(events=events)
        n = SlackNotifier(cfg)
        n.notify(NotifyEvent.ON_OPEN, {"symbol": "ETHUSDT"})
        assert len(calls) == 0

    def test_api_ok_false_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ok=false 응답도 예외 없이 처리."""
        def mock_post(*args, **kwargs):
            return _FakeResponse(200, ok=False)

        monkeypatch.setattr(requests, "post", mock_post)
        cfg = _make_slack_config()
        n = SlackNotifier(cfg)
        n.notify(NotifyEvent.ON_CLOSE, {"symbol": "SOLUSDT", "side": "SHORT", "filled_price": 100.0, "realized_pnl": -5.0})

    def test_dataclass_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dataclass payload 가 정상 직렬화된다."""
        import dataclasses

        @dataclasses.dataclass
        class FakeOpen:
            symbol: str = "BTCUSDT"
            side: str = "LONG"
            leverage: int = 5
            filled_price: float = 65000.0
            margin_usdt: float = 200.0
            liquidation_price: float = 50000.0

        calls: list = []

        def mock_post(*args, **kwargs):
            calls.append(kwargs.get("json", {}))
            return _FakeResponse(200, ok=True)

        monkeypatch.setattr(requests, "post", mock_post)
        cfg = _make_slack_config()
        n = SlackNotifier(cfg)
        n.notify(NotifyEvent.ON_OPEN, FakeOpen())
        assert len(calls) == 1
        # blocks 키가 존재해야 함
        assert "blocks" in calls[0]
