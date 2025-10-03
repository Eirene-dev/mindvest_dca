# adaptive/market_state.py
from typing import Any, Dict, List, Optional, Callable, Awaitable
from datetime import datetime
import logging


class MarketStateManager:
    """
    시장 상태 관리자 (분리 버전)
    - DB에서 상태를 읽어 업데이트
    - 상태 전환 시 트레이더 콜백을 통해 포지션 감축/청산 수행
    - 알림은 외부에서 주입된 send_message 콜백으로 처리
    """

    def __init__(
        self,
        *,
        db_manager: Any,
        symbol_risk_tiers: Dict[str, List[str]],
        state_strategy_matrix: Dict[str, Dict[str, Any]],
        send_message: Optional[Callable[[str], Awaitable[None]]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.db = db_manager
        self.symbol_risk_tiers = symbol_risk_tiers
        self.state_strategy_matrix = state_strategy_matrix
        self._send_message = send_message
        self.logger = logger or logging.getLogger(__name__)

        self.current_state = "S4"
        self.last_state = "S4"
        self.state_confidence = 0.5
        self.state_changed_time = datetime.now()
        self.last_update_time = datetime.now()

        # 트레이더 동작 콜백 (나중에 주입)
        self._reduce_position_cb: Optional[Callable[[str, float], Awaitable[None]]] = None
        self._close_position_cb: Optional[Callable[[str], Awaitable[None]]] = None

    # 트레이더 포지션 제어 콜백 연결
    def set_trader_callbacks(
        self,
        reduce_position_cb: Callable[[str, float], Awaitable[None]],
        close_position_cb: Callable[[str], Awaitable[None]],
    ) -> None:
        self._reduce_position_cb = reduce_position_cb
        self._close_position_cb = close_position_cb

    def get_strategy_params(self) -> Dict[str, Any]:
        return self.state_strategy_matrix.get(self.current_state, self.state_strategy_matrix["S4"])

    async def update_state(self) -> bool:
        """DB에서 최신 시장 상태를 읽고 변경 시 알림/전환 처리"""
        try:
            market_data = self.db.get_latest_market_state()
            if market_data and "state" in market_data:
                self.last_state = self.current_state
                self.current_state = market_data["state"]
                self.state_confidence = market_data.get("confidence", 0.5)
                self.last_update_time = datetime.now()

                if self.current_state != self.last_state:
                    self.state_changed_time = datetime.now()
                    await self.notify_state_change(market_data)
                    await self.handle_state_transition()
                return True
        except Exception as e:
            self.logger.error(f"Error updating state: {e}")
        return False

    async def handle_state_transition(self) -> None:
        """상태 전환 시 위험자산 감축/청산 로직"""
        if not (self._reduce_position_cb and self._close_position_cb):
            self.logger.debug("Trader callbacks not set; skipping transition actions.")
            return

        # S6 -> S7: 고위험 알트 50% 감축
        if self.last_state == "S6" and self.current_state == "S7":
            self.logger.info("State transition S6->S7: Reducing high-risk alts by 50%")
            for symbol in self.symbol_risk_tiers.get("TIER_3", []):
                await self._reduce_position_cb(symbol, 0.5)

        # S7 -> S8: 고위험 알트 전량 청산
        elif self.last_state == "S7" and self.current_state == "S8":
            self.logger.info("State transition S7->S8: Closing all high-risk alts")
            for symbol in self.symbol_risk_tiers.get("TIER_3", []):
                await self._close_position_cb(symbol)

        # S5/S6 -> S0/S1/S2: 긴급 청산
        elif self.last_state in ["S5", "S6"] and self.current_state in ["S0", "S1", "S2"]:
            self.logger.warning("Emergency state transition: Closing all alt positions")
            for tier in ["TIER_3", "TIER_2"]:
                for symbol in self.symbol_risk_tiers.get(tier, []):
                    await self._close_position_cb(symbol)

    async def notify_state_change(self, market_data: Dict[str, Any]) -> None:
        """상태 변경 텔레그램 알림"""
        try:
            old_strategy = self.state_strategy_matrix.get(self.last_state, {})
            new_strategy = self.state_strategy_matrix.get(self.current_state, {})
            msg = f"""
🔄 **Market State Changed**
━━━━━━━━━━━━━━━━━━━━
From: {self.last_state} ({old_strategy.get('description','')})
To: {self.current_state} ({new_strategy.get('description','')})
━━━━━━━━━━━━━━━━━━━━
📊 Confidence: {self.state_confidence:.1%}
💰 BTC Price: ${market_data.get('btc_price', 0):,.0f}
📈 Breadth: {market_data.get('breadth_above50', 0.5):.1%}
━━━━━━━━━━━━━━━━━━━━
Amount Multiplier: {new_strategy.get('amount_multiplier')}
Trading Interval: {new_strategy.get('interval_hours')}h
Allowed Symbols: {', '.join(new_strategy.get('allowed_symbols', []))}
"""
            if self._send_message:
                await self._send_message(msg)
            self.logger.info(f"Market state changed: {self.last_state} -> {self.current_state}")
        except Exception as e:
            self.logger.error(f"Error in notify_state_change: {e}")
