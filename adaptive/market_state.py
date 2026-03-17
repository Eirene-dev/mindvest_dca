#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Market State Manager (Simplified)
DB에서 S0-S8 상태를 읽고 3-Mode(STOP/NORMAL/CAUTIOUS)로 변환합니다.
"""

from typing import Any, Dict, List, Optional, Callable, Awaitable
from datetime import datetime
import logging
import html

from adaptive.policies import (
    TradingMode,
    SYMBOL_RISK_TIERS,
    get_mode,
    get_mode_params,
)


class MarketStateManager:
    """
    시장 상태 관리자 (단순화 버전)
    - DB에서 S0-S8 읽기
    - 3-Mode 변환
    - 상태 전환 시 알트코인 청산 트리거
    """

    def __init__(
        self,
        *,
        db_manager,
        send_message: Optional[Callable[[str], Awaitable[None]]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.db = db_manager
        self._send_message = send_message
        self.logger = logger or logging.getLogger(__name__)

        # 상태 필드
        self.current_state: str = "S4"
        self.last_state: str = "S4"
        self.state_confidence: float = 0.5
        self.last_update_time: datetime = datetime.now()

        # 트레이더 콜백 (포지션 감축/청산)
        self._reduce_position_cb: Optional[Callable[[str, float], Awaitable[None]]] = None
        self._close_position_cb: Optional[Callable[[str], Awaitable[None]]] = None

    # ---------------------------
    # 속성
    # ---------------------------
    @property
    def current_mode(self) -> TradingMode:
        return get_mode(self.current_state)

    @property
    def mode_params(self) -> Dict[str, Any]:
        return get_mode_params(self.current_state)

    # ---------------------------
    # 콜백 연결
    # ---------------------------
    def set_trader_callbacks(
        self,
        reduce_position_cb: Callable[[str, float], Awaitable[None]],
        close_position_cb: Callable[[str], Awaitable[None]],
    ) -> None:
        self._reduce_position_cb = reduce_position_cb
        self._close_position_cb = close_position_cb

    # ---------------------------
    # 상태 업데이트
    # ---------------------------
    async def update_state(self) -> bool:
        """DB에서 최신 상태를 읽고, 모드 변경 시 알림/전환 처리"""
        try:
            market_data = self.db.get_latest_market_state()
            if not market_data or "state" not in market_data:
                self.logger.warning("No market state from DB, keeping current")
                return False

            self.last_state = self.current_state
            self.current_state = market_data["state"]
            self.state_confidence = market_data.get("confidence", 0.5)
            self.last_update_time = datetime.now()

            old_mode = get_mode(self.last_state)
            new_mode = get_mode(self.current_state)

            # 모드 변경 시에만 알림 + 전환 로직
            if old_mode != new_mode:
                await self._notify_mode_change(market_data, old_mode, new_mode)
                await self._handle_mode_transition(old_mode, new_mode)
            elif self.current_state != self.last_state:
                # 같은 모드 내 상태 변경은 간단 로그만
                self.logger.info(
                    f"State changed {self.last_state} → {self.current_state} "
                    f"(same mode: {new_mode.value})"
                )

            return True

        except Exception as e:
            self.logger.error(f"Error updating state: {e}")
            return False

    # ---------------------------
    # 모드 전환 처리
    # ---------------------------
    async def _handle_mode_transition(
        self, old_mode: TradingMode, new_mode: TradingMode
    ) -> None:
        """모드 전환 시 포지션 정리 로직"""
        if not (self._reduce_position_cb and self._close_position_cb):
            self.logger.debug("Trader callbacks not set; skipping transition actions")
            return

        # NORMAL/CAUTIOUS → STOP: 알트 전량 청산
        if new_mode == TradingMode.STOP:
            self.logger.warning("Mode → STOP: Closing all alt positions")
            for tier in ["TIER_3", "TIER_2"]:
                for symbol in SYMBOL_RISK_TIERS.get(tier, []):
                    await self._close_position_cb(symbol)

        # NORMAL → CAUTIOUS: 고위험 알트 50% 감축
        elif old_mode == TradingMode.NORMAL and new_mode == TradingMode.CAUTIOUS:
            self.logger.info("Mode → CAUTIOUS: Reducing TIER_3 alts by 50%")
            for symbol in SYMBOL_RISK_TIERS.get("TIER_3", []):
                await self._reduce_position_cb(symbol, 0.5)

    # ---------------------------
    # 알림
    # ---------------------------
    async def _notify_mode_change(
        self,
        market_data: Dict[str, Any],
        old_mode: TradingMode,
        new_mode: TradingMode,
    ) -> None:
        """텔레그램 모드 변경 알림"""
        try:
            params = get_mode_params(self.current_state)
            btc_price = f"${market_data.get('btc_price', 0):,.0f}"
            confidence = f"{self.state_confidence:.1%}"

            msg = (
                "🔄 <b>Trading Mode Changed</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"State: <code>{html.escape(self.last_state)}</code>"
                f" → <code>{html.escape(self.current_state)}</code>\n"
                f"Mode: <b>{html.escape(old_mode.value)}</b>"
                f" → <b>{html.escape(new_mode.value)}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 {html.escape(params['description'])}\n"
                f"• Amount: {params['amount_multiplier']}x\n"
                f"• Interval: {params['interval_hours']}h\n"
                f"• BTC Price: {btc_price}\n"
                f"• Confidence: {confidence}\n"
            )

            if self._send_message:
                await self._send_message(msg)

            self.logger.info(
                f"Mode changed: {old_mode.value} → {new_mode.value} "
                f"(state {self.last_state} → {self.current_state})"
            )
        except Exception as e:
            self.logger.error(f"Error in notify_mode_change: {e}")