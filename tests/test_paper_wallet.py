"""
PaperWallet / ExecutionEngine 단위 테스트.
실행: pytest tests/test_paper_wallet.py -v
수치는 모두 손계산 후 pytest.approx로 검증.
"""

import math
import pytest
from core.paper_wallet import (
    AccountState,
    CloseResult,
    ExecutionEngine,
    ExecutionResult,
    OpenResult,
    PaperWallet,
    Position,
    PositionSide,
)


# ──────────────────────────────────────────────
# 공통 픽스처
# ──────────────────────────────────────────────

@pytest.fixture
def wallet() -> PaperWallet:
    """기본 지갑: 잔고 1000 USDT, 수수료 0.05%, 슬리피지 없음, max_lev 10."""
    return PaperWallet(
        initial_balance_usdt=1000.0,
        taker_fee_rate=0.0005,
        slippage_bp=0.0,
        maintenance_margin_rate=0.005,
        max_leverage=10,
    )


@pytest.fixture
def wallet_slip() -> PaperWallet:
    """슬리피지 1bp 지갑."""
    return PaperWallet(
        initial_balance_usdt=1000.0,
        taker_fee_rate=0.0005,
        slippage_bp=1.0,
        maintenance_margin_rate=0.005,
        max_leverage=10,
    )


# ──────────────────────────────────────────────
# 1. 청산가 공식 수치 검증
# ──────────────────────────────────────────────

def test_liquidation_price_long() -> None:
    """롱 청산가 = entry*(1 - 1/lev + mmr)."""
    liq = ExecutionEngine.liquidation_price(PositionSide.LONG, 1000.0, 5, 0.005)
    expected = 1000.0 * (1.0 - 1.0 / 5 + 0.005)  # 1000*(0.805) = 805.0
    assert liq == pytest.approx(expected, rel=1e-9)


def test_liquidation_price_short() -> None:
    """숏 청산가 = entry*(1 + 1/lev - mmr)."""
    liq = ExecutionEngine.liquidation_price(PositionSide.SHORT, 1000.0, 5, 0.005)
    expected = 1000.0 * (1.0 + 1.0 / 5 - 0.005)  # 1000*(1.195) = 1195.0
    assert liq == pytest.approx(expected, rel=1e-9)


# ──────────────────────────────────────────────
# 2. 미실현 손익 부호
# ──────────────────────────────────────────────

def test_unrealized_pnl_long_profit() -> None:
    """롱 가격 상승 → 양수."""
    pnl = ExecutionEngine.unrealized_pnl(PositionSide.LONG, 1000.0, 1100.0, 2.0)
    assert pnl == pytest.approx(200.0)


def test_unrealized_pnl_long_loss() -> None:
    """롱 가격 하락 → 음수."""
    pnl = ExecutionEngine.unrealized_pnl(PositionSide.LONG, 1000.0, 900.0, 2.0)
    assert pnl == pytest.approx(-200.0)


def test_unrealized_pnl_short_profit() -> None:
    """숏 가격 하락 → 양수."""
    pnl = ExecutionEngine.unrealized_pnl(PositionSide.SHORT, 1000.0, 900.0, 2.0)
    assert pnl == pytest.approx(200.0)


def test_unrealized_pnl_short_loss() -> None:
    """숏 가격 상승 → 음수."""
    pnl = ExecutionEngine.unrealized_pnl(PositionSide.SHORT, 1000.0, 1100.0, 2.0)
    assert pnl == pytest.approx(-200.0)


# ──────────────────────────────────────────────
# 3. 순수 함수 stateless 검증
# ──────────────────────────────────────────────

def test_engine_calculate_open_stateless() -> None:
    """같은 인자로 두 번 호출해도 결과가 동일 (외부 상태 없음)."""
    r1 = ExecutionEngine.calculate_open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    r2 = ExecutionEngine.calculate_open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    assert r1.filled_price == pytest.approx(r2.filled_price)
    assert r1.quantity == pytest.approx(r2.quantity)
    assert r1.fee_usdt == pytest.approx(r2.fee_usdt)


def test_engine_calculate_close_stateless() -> None:
    """calculate_close 순수 함수 확인."""
    r1 = ExecutionEngine.calculate_close("BTCUSDT", PositionSide.LONG, 55000.0, 0.006, 50000.0, 100.0)
    r2 = ExecutionEngine.calculate_close("BTCUSDT", PositionSide.LONG, 55000.0, 0.006, 50000.0, 100.0)
    assert r1.realized_pnl == pytest.approx(r2.realized_pnl)


# ──────────────────────────────────────────────
# 4. 롱 진입 — 잔고/qty/notional/청산가
# ──────────────────────────────────────────────

def test_long_open_balance_deducted(wallet: PaperWallet) -> None:
    """진입 후 잔고 = 1000 - (margin + fee). fee = notional*fee_rate = margin*lev*rate."""
    r = wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    assert r.success
    notional = 100.0 * 3  # 300
    fee = notional * 0.0005  # 0.15
    expected_balance = 1000.0 - 100.0 - fee
    assert wallet.get_balance() == pytest.approx(expected_balance, rel=1e-9)


def test_long_open_quantity(wallet: PaperWallet) -> None:
    """quantity = notional / filled_price."""
    r = wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    assert r.success
    detail: OpenResult = r.detail  # type: ignore[assignment]
    expected_qty = (100.0 * 3) / 50000.0  # 0.006
    assert detail.quantity == pytest.approx(expected_qty, rel=1e-9)


def test_long_open_liquidation_price(wallet: PaperWallet) -> None:
    """롱 청산가 공식: entry*(1-1/lev+mmr)."""
    r = wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    assert r.success
    pos = wallet.get_position("BTCUSDT")
    assert pos is not None
    expected_liq = 50000.0 * (1.0 - 1.0 / 3 + 0.005)
    assert pos.liquidation_price == pytest.approx(expected_liq, rel=1e-9)


# ──────────────────────────────────────────────
# 5. 숏 진입 — 청산가가 진입가 위쪽
# ──────────────────────────────────────────────

def test_short_open_liquidation_price_above_entry(wallet: PaperWallet) -> None:
    """숏 청산가는 진입가보다 높아야 함."""
    r = wallet.open("ETHUSDT", PositionSide.SHORT, 3000.0, 100.0, 5)
    assert r.success
    pos = wallet.get_position("ETHUSDT")
    assert pos is not None
    assert pos.liquidation_price > 3000.0


def test_short_open_liquidation_price_value(wallet: PaperWallet) -> None:
    """숏 청산가 수치 검증: entry*(1+1/lev-mmr)."""
    r = wallet.open("ETHUSDT", PositionSide.SHORT, 3000.0, 100.0, 5)
    assert r.success
    pos = wallet.get_position("ETHUSDT")
    assert pos is not None
    expected_liq = 3000.0 * (1.0 + 1.0 / 5 - 0.005)
    assert pos.liquidation_price == pytest.approx(expected_liq, rel=1e-9)


# ──────────────────────────────────────────────
# 6. 증액 — 가중평균 entry
# ──────────────────────────────────────────────

def test_add_to_long_weighted_avg_entry(wallet: PaperWallet) -> None:
    """
    1차: 100 USDT @ 50000, lev=3 → qty1=0.006, entry=50000
    2차: 100 USDT @ 51000, lev=3 → qty2=300/51000≈0.005882
    가중평균 entry = (qty1*50000 + qty2*51000) / (qty1+qty2)
    """
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    wallet.open("BTCUSDT", PositionSide.LONG, 51000.0, 100.0, 3)
    pos = wallet.get_position("BTCUSDT")
    assert pos is not None
    qty1 = 300.0 / 50000.0
    qty2 = 300.0 / 51000.0
    expected_entry = (qty1 * 50000.0 + qty2 * 51000.0) / (qty1 + qty2)
    assert pos.entry_price == pytest.approx(expected_entry, rel=1e-6)


def test_add_to_long_qty_sum(wallet: PaperWallet) -> None:
    """증액 후 quantity = qty1 + qty2."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    wallet.open("BTCUSDT", PositionSide.LONG, 51000.0, 100.0, 3)
    pos = wallet.get_position("BTCUSDT")
    assert pos is not None
    qty1 = 300.0 / 50000.0
    qty2 = 300.0 / 51000.0
    assert pos.quantity == pytest.approx(qty1 + qty2, rel=1e-9)


# ──────────────────────────────────────────────
# 7. 롱 청산 — 이익/손실 realized_pnl
# ──────────────────────────────────────────────

def test_long_close_profit_realized(wallet: PaperWallet) -> None:
    """
    진입: 100 USDT @ 50000, lev=3 → qty=0.006
    청산: @ 55000 (상승)
    gross_pnl = (55000-50000)*0.006 = 30.0
    fee = 0.006*55000*0.0005 = 0.165
    realized = 30.0 - 0.165 = 29.835
    """
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    r = wallet.close("BTCUSDT", 55000.0)
    assert r.success
    detail: CloseResult = r.detail  # type: ignore[assignment]
    qty = 300.0 / 50000.0  # 0.006
    gross = (55000.0 - 50000.0) * qty
    fee = qty * 55000.0 * 0.0005
    expected_pnl = gross - fee
    assert detail.realized_pnl == pytest.approx(expected_pnl, rel=1e-9)


def test_long_close_loss_realized(wallet: PaperWallet) -> None:
    """롱 청산 손실: 가격 하락 시 realized_pnl < 0."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    r = wallet.close("BTCUSDT", 48000.0)
    assert r.success
    detail: CloseResult = r.detail  # type: ignore[assignment]
    assert detail.realized_pnl < 0.0


# ──────────────────────────────────────────────
# 8. 숏 청산 — 이익/손실
# ──────────────────────────────────────────────

def test_short_close_profit_realized(wallet: PaperWallet) -> None:
    """
    숏 진입: 100 USDT @ 3000, lev=5 → notional=500, qty=500/3000≈0.1667
    청산: @ 2800 (하락)
    gross = (3000-2800)*qty
    fee = qty*2800*0.0005
    realized = gross - fee
    """
    wallet.open("ETHUSDT", PositionSide.SHORT, 3000.0, 100.0, 5)
    pos = wallet.get_position("ETHUSDT")
    assert pos is not None
    qty = pos.quantity
    r = wallet.close("ETHUSDT", 2800.0)
    assert r.success
    detail: CloseResult = r.detail  # type: ignore[assignment]
    gross = (3000.0 - 2800.0) * qty
    fee = qty * 2800.0 * 0.0005
    expected_pnl = gross - fee
    assert detail.realized_pnl == pytest.approx(expected_pnl, rel=1e-9)


def test_short_close_loss_realized(wallet: PaperWallet) -> None:
    """숏 청산 손실: 가격 상승 시 realized_pnl < 0."""
    wallet.open("ETHUSDT", PositionSide.SHORT, 3000.0, 100.0, 5)
    r = wallet.close("ETHUSDT", 3300.0)
    assert r.success
    detail: CloseResult = r.detail  # type: ignore[assignment]
    assert detail.realized_pnl < 0.0


# ──────────────────────────────────────────────
# 9. 부분청산 — 비례 처리
# ──────────────────────────────────────────────

def test_partial_close_proportional_margin(wallet: PaperWallet) -> None:
    """부분청산 50% → 남은 margin = 원래의 50%."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    pos_before = wallet.get_position("BTCUSDT")
    assert pos_before is not None
    half_qty = pos_before.quantity / 2.0
    wallet.close("BTCUSDT", 50000.0, quantity=half_qty)
    pos_after = wallet.get_position("BTCUSDT")
    assert pos_after is not None
    assert pos_after.margin_usdt == pytest.approx(pos_before.margin_usdt / 2.0, rel=1e-9)


def test_partial_close_remaining_qty(wallet: PaperWallet) -> None:
    """부분청산 후 남은 quantity 검증."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    pos_before = wallet.get_position("BTCUSDT")
    assert pos_before is not None
    half_qty = pos_before.quantity / 2.0
    wallet.close("BTCUSDT", 50000.0, quantity=half_qty)
    pos_after = wallet.get_position("BTCUSDT")
    assert pos_after is not None
    assert pos_after.quantity == pytest.approx(half_qty, rel=1e-9)


def test_full_close_removes_position(wallet: PaperWallet) -> None:
    """전량 청산 후 positions에서 키 삭제."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    wallet.close("BTCUSDT", 50000.0)
    assert wallet.get_position("BTCUSDT") is None


# ──────────────────────────────────────────────
# 10. 강제청산 (check_liquidations)
# ──────────────────────────────────────────────

def test_liquidation_long_triggered(wallet: PaperWallet) -> None:
    """롱 mark_price <= liq_price → 강제청산."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    pos = wallet.get_position("BTCUSDT")
    assert pos is not None
    liq_price = pos.liquidation_price
    results = wallet.check_liquidations({"BTCUSDT": liq_price - 1.0})
    assert len(results) == 1
    assert results[0].success
    detail: CloseResult = results[0].detail  # type: ignore[assignment]
    assert detail.is_liquidation is True
    assert wallet.get_position("BTCUSDT") is None


def test_liquidation_long_not_triggered(wallet: PaperWallet) -> None:
    """롱 mark_price > liq_price → 청산 없음."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    results = wallet.check_liquidations({"BTCUSDT": 55000.0})
    assert len(results) == 0


def test_liquidation_short_triggered(wallet: PaperWallet) -> None:
    """숏 mark_price >= liq_price → 강제청산."""
    wallet.open("ETHUSDT", PositionSide.SHORT, 3000.0, 100.0, 5)
    pos = wallet.get_position("ETHUSDT")
    assert pos is not None
    liq_price = pos.liquidation_price
    results = wallet.check_liquidations({"ETHUSDT": liq_price + 1.0})
    assert len(results) == 1
    assert results[0].success
    detail: CloseResult = results[0].detail  # type: ignore[assignment]
    assert detail.is_liquidation is True


def test_liquidation_loss_approx_margin(wallet: PaperWallet) -> None:
    """강제청산 시 잔고 ≈ initial - margin (fee 포함해도 거의 0 이익). balance < initial - margin이어야."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 200.0, 3)
    # 진입 후 잔고
    bal_after_open = wallet.get_balance()
    pos = wallet.get_position("BTCUSDT")
    assert pos is not None
    wallet.check_liquidations({"BTCUSDT": pos.liquidation_price})
    bal_after_liq = wallet.get_balance()
    # 청산 후 잔고는 진입 후 잔고보다 적어야 (손실 발생)
    assert bal_after_liq < bal_after_open + pos.margin_usdt


# ──────────────────────────────────────────────
# 11. 거부 케이스
# ──────────────────────────────────────────────

def test_reject_insufficient_balance(wallet: PaperWallet) -> None:
    r = wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 2000.0, 3)
    assert not r.success
    assert r.rejection_reason == "INSUFFICIENT_BALANCE"


def test_reject_min_notional(wallet: PaperWallet) -> None:
    """notional = 1*1 = 1 USDT < 5 USDT."""
    r = wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 1.0, 1)
    assert not r.success
    assert r.rejection_reason == "MIN_NOTIONAL_NOT_MET"


def test_reject_opposite_position(wallet: PaperWallet) -> None:
    """롱 보유 중 숏 진입 시도 → OPPOSITE_POSITION_EXISTS."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    r = wallet.open("BTCUSDT", PositionSide.SHORT, 50000.0, 100.0, 3)
    assert not r.success
    assert r.rejection_reason == "OPPOSITE_POSITION_EXISTS"


def test_reject_leverage_exceeded() -> None:
    w = PaperWallet(initial_balance_usdt=1000.0, max_leverage=5)
    r = w.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 10)
    assert not r.success
    assert r.rejection_reason == "LEVERAGE_EXCEEDED"


def test_reject_no_position(wallet: PaperWallet) -> None:
    r = wallet.close("BTCUSDT", 50000.0)
    assert not r.success
    assert r.rejection_reason == "NO_POSITION"


def test_reject_insufficient_quantity(wallet: PaperWallet) -> None:
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    pos = wallet.get_position("BTCUSDT")
    assert pos is not None
    r = wallet.close("BTCUSDT", 50000.0, quantity=pos.quantity + 1.0)
    assert not r.success
    assert r.rejection_reason == "INSUFFICIENT_QUANTITY"


def test_reject_invalid_price_open(wallet: PaperWallet) -> None:
    r = wallet.open("BTCUSDT", PositionSide.LONG, 0.0, 100.0, 3)
    assert not r.success
    assert r.rejection_reason == "INVALID_PRICE"


def test_reject_invalid_price_close(wallet: PaperWallet) -> None:
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    r = wallet.close("BTCUSDT", 0.0)
    assert not r.success
    assert r.rejection_reason == "INVALID_PRICE"


def test_reject_invalid_margin(wallet: PaperWallet) -> None:
    r = wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, -10.0, 3)
    assert not r.success
    assert r.rejection_reason == "INVALID_MARGIN"


# ──────────────────────────────────────────────
# 12. 슬리피지 방향
# ──────────────────────────────────────────────

def test_slippage_long_open_higher_price(wallet_slip: PaperWallet) -> None:
    """롱 진입 filled > 시세 (슬리피지 불리)."""
    r = wallet_slip.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    assert r.success
    detail: OpenResult = r.detail  # type: ignore[assignment]
    assert detail.filled_price > 50000.0


def test_slippage_short_open_lower_price(wallet_slip: PaperWallet) -> None:
    """숏 진입 filled < 시세 (슬리피지 불리)."""
    r = wallet_slip.open("ETHUSDT", PositionSide.SHORT, 3000.0, 100.0, 3)
    assert r.success
    detail: OpenResult = r.detail  # type: ignore[assignment]
    assert detail.filled_price < 3000.0


def test_slippage_long_close_lower_price(wallet_slip: PaperWallet) -> None:
    """롱 청산 filled < 시세 (슬리피지 불리)."""
    wallet_slip.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    r = wallet_slip.close("BTCUSDT", 55000.0)
    assert r.success
    detail: CloseResult = r.detail  # type: ignore[assignment]
    assert detail.filled_price < 55000.0


# ──────────────────────────────────────────────
# 13. get_equity / get_wallet_balance
# ──────────────────────────────────────────────

def test_wallet_balance_includes_margin(wallet: PaperWallet) -> None:
    """wallet_balance = free + margin."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    pos = wallet.get_position("BTCUSDT")
    assert pos is not None
    assert wallet.get_wallet_balance() == pytest.approx(
        wallet.get_balance() + pos.margin_usdt, rel=1e-9
    )


def test_equity_includes_unrealized_pnl(wallet: PaperWallet) -> None:
    """equity = wallet_balance + upnl."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    pos = wallet.get_position("BTCUSDT")
    assert pos is not None
    prices = {"BTCUSDT": 55000.0}
    upnl = (55000.0 - pos.entry_price) * pos.quantity
    expected_equity = wallet.get_wallet_balance() + upnl
    assert wallet.get_equity(prices) == pytest.approx(expected_equity, rel=1e-9)


# ──────────────────────────────────────────────
# 14. snapshot 복원
# ──────────────────────────────────────────────

def test_snapshot_is_deep_copy(wallet: PaperWallet) -> None:
    """snapshot 후 지갑 변경해도 스냅샷에 영향 없음."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    snap = wallet.snapshot()
    balance_snap = snap.balance_usdt
    positions_snap = dict(snap.positions)

    wallet.close("BTCUSDT", 55000.0)  # 상태 변경

    assert snap.balance_usdt == pytest.approx(balance_snap, rel=1e-9)
    assert "BTCUSDT" in positions_snap


def test_snapshot_restores_state() -> None:
    """snapshot으로 AccountState를 복원해 새 지갑 초기화 시 동일 결과."""
    w1 = PaperWallet(initial_balance_usdt=1000.0)
    w1.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    snap = w1.snapshot()

    w2 = PaperWallet(initial_balance_usdt=0.0, state=snap)
    assert w2.get_balance() == pytest.approx(w1.get_balance(), rel=1e-9)
    pos2 = w2.get_position("BTCUSDT")
    pos1 = w1.get_position("BTCUSDT")
    assert pos2 is not None and pos1 is not None
    assert pos2.quantity == pytest.approx(pos1.quantity, rel=1e-9)


# ──────────────────────────────────────────────
# 15. Engine 결과 = Wallet 결과 일치
# ──────────────────────────────────────────────

def test_engine_open_matches_wallet_open(wallet: PaperWallet) -> None:
    """ExecutionEngine.calculate_open 결과가 wallet.open detail과 동일."""
    engine_result = ExecutionEngine.calculate_open(
        "BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3,
        taker_fee_rate=0.0005, slippage_bp=0.0, maintenance_margin_rate=0.005,
    )
    wallet_result = wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    assert wallet_result.success
    detail: OpenResult = wallet_result.detail  # type: ignore[assignment]
    assert detail.filled_price == pytest.approx(engine_result.filled_price, rel=1e-9)
    assert detail.quantity == pytest.approx(engine_result.quantity, rel=1e-9)
    assert detail.fee_usdt == pytest.approx(engine_result.fee_usdt, rel=1e-9)


def test_engine_close_matches_wallet_close(wallet: PaperWallet) -> None:
    """ExecutionEngine.calculate_close 결과가 wallet.close detail과 동일."""
    wallet.open("BTCUSDT", PositionSide.LONG, 50000.0, 100.0, 3)
    pos = wallet.get_position("BTCUSDT")
    assert pos is not None

    engine_result = ExecutionEngine.calculate_close(
        "BTCUSDT", PositionSide.LONG, 55000.0,
        pos.quantity, pos.entry_price, pos.margin_usdt,
        taker_fee_rate=0.0005, slippage_bp=0.0, is_liquidation=False,
    )
    wallet_result = wallet.close("BTCUSDT", 55000.0)
    assert wallet_result.success
    detail: CloseResult = wallet_result.detail  # type: ignore[assignment]
    assert detail.realized_pnl == pytest.approx(engine_result.realized_pnl, rel=1e-9)
    assert detail.fee_usdt == pytest.approx(engine_result.fee_usdt, rel=1e-9)
