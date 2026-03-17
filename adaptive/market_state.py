# adaptive/market_state.py
from typing import Any, Dict, List, Optional, Callable, Awaitable, Tuple
from datetime import datetime
from enum import Enum
import logging
import html

class MarketRegime(Enum):
    """시장 국면 분류"""
    CAPITULATION = "Capitulation"           # 극단 하락
    ACCUMULATION = "Accumulation"           # 바닥 누적
    EARLY_RECOVERY = "Early Recovery"       # 초기 회복
    CONFIRMED_UPTREND = "Confirmed Uptrend" # 상승 확정
    LATE_BULL = "Late Bull"                 # 상승 후기
    DISTRIBUTION = "Distribution"           # 분배
    EARLY_DECLINE = "Early Decline"         # 하락 초기
    CONFIRMED_DOWNTREND = "Confirmed Down"  # 하락 확정

# MarketRegime별 DCA 파라미터
REGIME_DCA_PARAMS = {
    MarketRegime.CAPITULATION: {
        'enabled': True,
        'amount_multiplier': 2.5,      # 공격적 매수
        'max_amount_multiplier': 1.5,  
        'tp_ratio': 15,                # TP 높게
        'sl_ratio': -5,                # SL 넓게
        'interval_hours': 4,           # 빠른 진입
        'allowed_symbols': ['BTC', 'ETH', 'SOL', 'LINK'],
        'symbol_weights': {'BTC': 1.0, 'ETH': 1.0, 'SOL': 0.8, 'LINK': 0.7},
        'max_total_exposure': 1.0,
        'max_alt_exposure': 0.3,
        'description': '극단 하락 - 공격적 매수'
    },
    MarketRegime.ACCUMULATION: {
        'enabled': True,
        'amount_multiplier': 2.0,
        'max_amount_multiplier': 1.3,
        'tp_ratio': 13,
        'sl_ratio': -7,
        'interval_hours': 6,
        'allowed_symbols': ['BTC', 'ETH', 'SOL', 'LINK', 'XRP'],
        'symbol_weights': {'BTC': 1.0, 'ETH': 1.0, 'SOL': 0.9, 'LINK': 0.8, 'XRP': 0.5},
        'max_total_exposure': 0.9,
        'max_alt_exposure': 0.4,
        'description': '바닥 누적 - 적극 매수'
    },
    MarketRegime.EARLY_RECOVERY: {
        'enabled': True,
        'amount_multiplier': 1.5,
        'max_amount_multiplier': 1.2,
        'tp_ratio': 12,
        'sl_ratio': -8,
        'interval_hours': 6,
        'allowed_symbols': ['BTC', 'ETH', 'SOL', 'LINK', 'XRP', 'ADA'],
        'symbol_weights': {'BTC': 1.0, 'ETH': 1.0, 'SOL': 0.9, 'LINK': 0.8, 'XRP': 0.6, 'ADA': 0.5},
        'max_total_exposure': 0.8,
        'max_alt_exposure': 0.4,
        'description': '초기 회복 - 표준 매수'
    },
    MarketRegime.CONFIRMED_UPTREND: {
        'enabled': True,
        'amount_multiplier': 1.0,
        'max_amount_multiplier': 1.0,
        'tp_ratio': 10,
        'sl_ratio': -10,
        'interval_hours': 8,
        'allowed_symbols': ['BTC', 'ETH', 'SOL', 'LINK', 'XRP', 'ADA', 'DOGE'],
        'symbol_weights': {
            'BTC': 1.0, 'ETH': 1.0,
            'SOL': 0.9, 'LINK': 0.8,
            'XRP': 0.6, 'ADA': 0.5, 'DOGE': 0.3
        },
        'max_total_exposure': 0.7,
        'max_alt_exposure': 0.4,
        'description': '상승 확정 - 정상 매수'
    },
    MarketRegime.LATE_BULL: {
        'enabled': True,
        'amount_multiplier': 0.7,      # 매수 축소
        'max_amount_multiplier': 0.8,
        'tp_ratio': 7,                 # TP 낮게
        'sl_ratio': -12,
        'interval_hours': 12,
        'allowed_symbols': ['BTC', 'ETH', 'SOL'],
        'symbol_weights': {'BTC': 1.0, 'ETH': 0.9, 'SOL': 0.7},
        'max_total_exposure': 0.5,
        'max_alt_exposure': 0.2,
        'description': '상승 후기 - 매수 축소'
    },
    MarketRegime.DISTRIBUTION: {
        'enabled': True,
        'amount_multiplier': 0.3,      # 최소 매수
        'max_amount_multiplier': 0.5,
        'tp_ratio': 5,
        'sl_ratio': -15,
        'interval_hours': 24,
        'allowed_symbols': ['BTC', 'ETH'],
        'symbol_weights': {'BTC': 1.0, 'ETH': 0.8},
        'max_total_exposure': 0.3,
        'max_alt_exposure': 0.0,
        'description': '분배 구간 - 최소 매수'
    },
    MarketRegime.EARLY_DECLINE: {
        'enabled': True,
        'amount_multiplier': 0.5,
        'max_amount_multiplier': 0.6,
        'tp_ratio': 8,
        'sl_ratio': -13,
        'interval_hours': 16,
        'allowed_symbols': ['BTC', 'ETH'],
        'symbol_weights': {'BTC': 1.0, 'ETH': 0.9},
        'max_total_exposure': 0.4,
        'max_alt_exposure': 0.1,
        'description': '하락 초기 - 방어적'
    },
    MarketRegime.CONFIRMED_DOWNTREND: {
        'enabled': False,               # 매수 중단
        'amount_multiplier': 0.0,
        'max_amount_multiplier': 0.4,
        'tp_ratio': 10,
        'sl_ratio': -20,
        'interval_hours': 24,
        'allowed_symbols': [],
        'symbol_weights': {},
        'max_total_exposure': 0.0,
        'max_alt_exposure': 0.0,
        'description': '하락 확정 - 매수 중단'
    }
}

class MarketStateManager:
    """
    시장 상태 관리자 (확장 버전)
    - state, sentiment, ai_signal을 모두 관리
    - MarketRegime 기반 파라미터 결정
    """

    def __init__(
        self,
        *,
        db_manager: Any,
        symbol_risk_tiers: Dict[str, List[str]],
        state_strategy_matrix: Dict[str, Dict[str, Any]],  # 기존 매트릭스는 백업용
        send_message: Optional[Callable[[str], Awaitable[None]]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.db = db_manager
        self.symbol_risk_tiers = symbol_risk_tiers
        self.state_strategy_matrix = state_strategy_matrix  # 백업용 유지
        self._send_message = send_message
        self.logger = logger or logging.getLogger(__name__)

        # 기존 state 관련 필드
        self.current_state = "S4"
        self.last_state = "S4"
        self.state_confidence = 0.5
        self.state_changed_time = datetime.now()
        self.last_update_time = datetime.now()

        # 새로운 필드 추가
        self.current_sentiment = "NEUTRAL"     # EXTREME_FEAR, FEAR, NEUTRAL, GREED, EXTREME_GREED
        self.current_ai_signal = "NEUTRAL"     # STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL
        self.current_regime = MarketRegime.CONFIRMED_UPTREND
        self.last_regime = MarketRegime.CONFIRMED_UPTREND
        self.regime_confidence = 0.5

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
        """현재 MarketRegime 기반 파라미터 반환"""
        return REGIME_DCA_PARAMS.get(
            self.current_regime, 
            REGIME_DCA_PARAMS[MarketRegime.CONFIRMED_UPTREND]
        )

    def map_sentiment_from_rsi_breadth(self, rsi: float, breadth: float) -> str:
        """RSI와 Market Breadth로부터 sentiment 추정 (간단한 규칙)"""
        if rsi < 20 and breadth < 0.2:
            return "EXTREME_FEAR"
        elif rsi < 35 and breadth < 0.35:
            return "FEAR"
        elif rsi > 80 and breadth > 0.8:
            return "EXTREME_GREED"
        elif rsi > 65 and breadth > 0.65:
            return "GREED"
        else:
            return "NEUTRAL"

    def determine_market_regime(
        self, 
        state: str, 
        sentiment: str, 
        ai_signal: str
    ) -> Tuple[MarketRegime, float]:
        """3개 지표를 종합해 시장 국면과 확신도 반환"""
        
        # 1. 극단 상황 우선 처리
        if state in ['S0', 'S1'] and sentiment == 'EXTREME_FEAR':
            if ai_signal == 'STRONG_SELL':
                return MarketRegime.CAPITULATION, 0.9
            else:
                return MarketRegime.ACCUMULATION, 0.7
        
        # 2. 상태 전환 감지
        transitions = self.detect_regime_transitions(state, sentiment, ai_signal)
        if transitions['turning_point']:
            return transitions['next_regime'], transitions['confidence']
        
        # 3. 일반 매핑 테이블
        regime_map = {
            # (State, Sentiment, AI) → Regime
            ('S2', 'EXTREME_FEAR', 'SELL'): MarketRegime.CONFIRMED_DOWNTREND,
            ('S2', 'FEAR', 'SELL'): MarketRegime.CONFIRMED_DOWNTREND,
            ('S3', 'FEAR', 'NEUTRAL'): MarketRegime.ACCUMULATION,
            ('S3', 'FEAR', 'BUY'): MarketRegime.EARLY_RECOVERY,
            ('S3', 'NEUTRAL', 'BUY'): MarketRegime.EARLY_RECOVERY,
            ('S4', 'NEUTRAL', 'BUY'): MarketRegime.EARLY_RECOVERY,
            ('S4', 'NEUTRAL', 'NEUTRAL'): MarketRegime.CONFIRMED_UPTREND,
            ('S5', 'NEUTRAL', 'BUY'): MarketRegime.CONFIRMED_UPTREND,
            ('S5', 'GREED', 'BUY'): MarketRegime.CONFIRMED_UPTREND,
            ('S6', 'GREED', 'BUY'): MarketRegime.CONFIRMED_UPTREND,
            ('S6', 'GREED', 'NEUTRAL'): MarketRegime.LATE_BULL,
            ('S6', 'EXTREME_GREED', 'NEUTRAL'): MarketRegime.LATE_BULL,
            ('S7', 'EXTREME_GREED', 'SELL'): MarketRegime.DISTRIBUTION,
            ('S7', 'GREED', 'STRONG_SELL'): MarketRegime.EARLY_DECLINE,
            ('S8', 'EXTREME_GREED', 'SELL'): MarketRegime.DISTRIBUTION,
        }
        
        # 4. 일치도 기반 확신도 계산
        alignment = self.calculate_alignment(state, sentiment, ai_signal)
        
        key = (state, sentiment, ai_signal)
        if key in regime_map:
            return regime_map[key], alignment
        
        # 5. 기본값 (state 기반 추정)
        if state >= 'S6':
            return MarketRegime.LATE_BULL, 0.5
        elif state >= 'S4':
            return MarketRegime.CONFIRMED_UPTREND, 0.5
        elif state >= 'S3':
            return MarketRegime.ACCUMULATION, 0.5
        else:
            return MarketRegime.CONFIRMED_DOWNTREND, 0.5

    def detect_regime_transitions(self, state: str, sentiment: str, ai_signal: str) -> dict:
        """시장 전환점 감지"""
        transitions = {
            'turning_point': False,
            'next_regime': None,
            'confidence': 0.0
        }
        
        # 바닥 전환 신호 (매수 강화 시점)
        bottom_signals = [
            state in ['S2', 'S3'] and sentiment == 'EXTREME_FEAR' and ai_signal in ['BUY', 'STRONG_BUY'],
            state == 'S3' and sentiment == 'FEAR' and ai_signal == 'STRONG_BUY',
            state == 'S4' and sentiment == 'EXTREME_FEAR' and ai_signal != 'STRONG_SELL'
        ]
        
        if any(bottom_signals):
            transitions['turning_point'] = True
            transitions['next_regime'] = MarketRegime.ACCUMULATION
            transitions['confidence'] = sum(1 for s in bottom_signals if s) / 3
            return transitions
        
        # 고점 전환 신호 (매수 축소 시점)
        top_signals = [
            state in ['S7', 'S8'] and sentiment == 'EXTREME_GREED',
            state == 'S6' and sentiment == 'EXTREME_GREED' and ai_signal in ['SELL', 'STRONG_SELL'],
            state >= 'S7' and ai_signal == 'STRONG_SELL'
        ]
        
        if any(top_signals):
            transitions['turning_point'] = True
            transitions['next_regime'] = MarketRegime.DISTRIBUTION
            transitions['confidence'] = sum(1 for s in top_signals if s) / 3
        
        return transitions

    def calculate_alignment(self, state: str, sentiment: str, ai_signal: str) -> float:
        """3개 지표의 일치도 계산 (0.0 ~ 1.0)"""
        score = 0.5  # 기본값
        
        # State와 Sentiment 일치도
        state_sentiment_map = {
            ('S0', 'EXTREME_FEAR'): 0.9,
            ('S1', 'EXTREME_FEAR'): 0.9,
            ('S2', 'FEAR'): 0.8,
            ('S3', 'FEAR'): 0.7,
            ('S4', 'NEUTRAL'): 0.8,
            ('S5', 'NEUTRAL'): 0.7,
            ('S6', 'GREED'): 0.8,
            ('S7', 'GREED'): 0.7,
            ('S8', 'EXTREME_GREED'): 0.9,
        }
        
        if (state, sentiment) in state_sentiment_map:
            score = state_sentiment_map[(state, sentiment)]
        
        # AI Signal 보정
        if state <= 'S3' and ai_signal in ['BUY', 'STRONG_BUY']:
            score *= 1.2  # 바닥에서 매수 신호
        elif state >= 'S6' and ai_signal in ['SELL', 'STRONG_SELL']:
            score *= 1.2  # 고점에서 매도 신호
        elif state <= 'S3' and ai_signal in ['SELL', 'STRONG_SELL']:
            score *= 0.8  # 바닥에서 매도 신호 (부조화)
        elif state >= 'S6' and ai_signal in ['BUY', 'STRONG_BUY']:
            score *= 0.8  # 고점에서 매수 신호 (부조화)
        
        return min(1.0, max(0.3, score))

    async def update_state(self) -> bool:
        """DB에서 최신 시장 상태를 읽고 변경 시 알림/전환 처리"""
        try:
            # 1. 기존 state 데이터 가져오기
            market_data = self.db.get_latest_market_state()
            if market_data and "state" in market_data:
                self.last_state = self.current_state
                self.current_state = market_data["state"]
                self.state_confidence = market_data.get("confidence", 0.5)
                
                # Sentiment 추정 (RSI와 Breadth 활용)
                rsi = market_data.get('rsi_btc', 50)
                breadth = market_data.get('breadth_above50', 0.5)
                self.current_sentiment = self.map_sentiment_from_rsi_breadth(rsi, breadth)
            
            # 2. AI 분석 데이터 가져오기
            self.current_ai_signal = 'NEUTRAL'
            # ai_data = self.db.get_latest_ai_analysis()
            # if ai_data:
            #     # overall_signal을 우리 포맷으로 변환
            #     signal_map = {
            #         'STRONG_BUY': 'STRONG_BUY',
            #         'BUY': 'BUY',
            #         'NEUTRAL': 'NEUTRAL',
            #         'SELL': 'SELL',
            #         'STRONG_SELL': 'STRONG_SELL'
            #     }
            #     raw_signal = ai_data.get('overall_signal', 'NEUTRAL').upper()
            #     self.current_ai_signal = signal_map.get(raw_signal, 'NEUTRAL')
            # else:
            #     # AI 데이터가 없으면 기본값
            #     self.current_ai_signal = 'NEUTRAL'
            
            # 3. MarketRegime 결정
            self.last_regime = self.current_regime
            self.current_regime, self.regime_confidence = self.determine_market_regime(
                self.current_state,
                self.current_sentiment,
                self.current_ai_signal
            )
            
            self.last_update_time = datetime.now()
            
            # 4. 변경 시 알림
            if self.current_state != self.last_state or self.current_regime != self.last_regime:
                self.state_changed_time = datetime.now()
                await self.notify_state_change(market_data)
                await self.handle_state_transition()
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating state: {e}")
        return False

    async def handle_state_transition(self) -> None:
        """MarketRegime 전환 시 위험자산 감축/청산 로직"""
        if not (self._reduce_position_cb and self._close_position_cb):
            self.logger.debug("Trader callbacks not set; skipping transition actions.")
            return

        # Regime 기반 전환 로직
        # Distribution/Distribution → Confirmed Downtrend: 모든 알트 청산
        if self.last_regime in [MarketRegime.DISTRIBUTION, MarketRegime.EARLY_DECLINE] and \
           self.current_regime == MarketRegime.CONFIRMED_DOWNTREND:
            self.logger.warning("Regime transition to CONFIRMED_DOWNTREND: Closing all alts")
            for tier in ["TIER_3", "TIER_2"]:
                for symbol in self.symbol_risk_tiers.get(tier, []):
                    await self._close_position_cb(symbol)
        
        # Late Bull → Distribution: 고위험 알트 50% 감축
        elif self.last_regime == MarketRegime.LATE_BULL and \
             self.current_regime == MarketRegime.DISTRIBUTION:
            self.logger.info("Regime transition to DISTRIBUTION: Reducing high-risk alts by 50%")
            for symbol in self.symbol_risk_tiers.get("TIER_3", []):
                await self._reduce_position_cb(symbol, 0.5)

        # 기존 state 기반 로직도 유지 (백업)
        if self.last_state == "S6" and self.current_state == "S7":
            self.logger.info("State transition S6->S7: Reducing high-risk alts by 50%")
            for symbol in self.symbol_risk_tiers.get("TIER_3", []):
                await self._reduce_position_cb(symbol, 0.5)

    async def notify_state_change(self, market_data: Dict[str, Any]) -> None:
        """상태 변경 텔레그램 알림 (HTML 포맷)"""
        try:
            # Regime 파라미터
            old_params = REGIME_DCA_PARAMS.get(self.last_regime)
            new_params = REGIME_DCA_PARAMS.get(self.current_regime)

            # 안전 이스케이프
            last_state = html.escape(self.last_state)
            current_state = html.escape(self.current_state)
            last_regime_str = html.escape(self.last_regime.value)
            current_regime_str = html.escape(self.current_regime.value)
            sentiment = html.escape(self.current_sentiment)
            ai_signal = html.escape(self.current_ai_signal)
            
            old_desc = html.escape(old_params.get("description", ""))
            new_desc = html.escape(new_params.get("description", ""))
            amount_mult = html.escape(str(new_params.get("amount_multiplier", "")))
            interval_hours = html.escape(str(new_params.get("interval_hours", "")))
            symbols = ", ".join(html.escape(s) for s in new_params.get("allowed_symbols", []))

            confidence = f"{self.regime_confidence:.1%}"
            btc_price = f"${market_data.get('btc_price', 0):,.0f}"
            breadth = f"{market_data.get('breadth_above50', 0.5):.1%}"
            rsi = f"{market_data.get('rsi_btc', 50):.1f}"

            msg = (
                "🔄 <b>Market Regime Changed</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>State:</b> <code>{last_state}</code> → <code>{current_state}</code>\n"
                f"<b>Regime:</b> {last_regime_str} → {current_regime_str}\n"
                f"<b>Description:</b> {new_desc}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Market Indicators</b>\n"
                f"• Sentiment: {sentiment}\n"
                f"• AI Signal: {ai_signal}\n"
                f"• Confidence: {confidence}\n"
                f"• BTC Price: {btc_price}\n"
                f"• RSI: {rsi}\n"
                f"• Breadth: {breadth}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>New Parameters</b>\n"
                f"• Amount Multiplier: {amount_mult}x\n"
                f"• Trading Interval: {interval_hours}h\n"
                f"• Allowed Symbols: {symbols}\n"
            )

            if self._send_message:
                await self._send_message(msg)

            self.logger.info(f"Market regime changed: {self.last_regime.value} -> {self.current_regime.value}")
        except Exception as e:
            self.logger.error(f"Error in notify_state_change: {e}")


# class MarketStateManager:
#     """
#     시장 상태 관리자 (분리 버전)
#     - DB에서 상태를 읽어 업데이트
#     - 상태 전환 시 트레이더 콜백을 통해 포지션 감축/청산 수행
#     - 알림은 외부에서 주입된 send_message 콜백으로 처리
#     """

#     def __init__(
#         self,
#         *,
#         db_manager: Any,
#         symbol_risk_tiers: Dict[str, List[str]],
#         state_strategy_matrix: Dict[str, Dict[str, Any]],
#         send_message: Optional[Callable[[str], Awaitable[None]]] = None,
#         logger: Optional[logging.Logger] = None,
#     ):
#         self.db = db_manager
#         self.symbol_risk_tiers = symbol_risk_tiers
#         self.state_strategy_matrix = state_strategy_matrix
#         self._send_message = send_message
#         self.logger = logger or logging.getLogger(__name__)

#         self.current_state = "S4"
#         self.last_state = "S4"
#         self.state_confidence = 0.5
#         self.state_changed_time = datetime.now()
#         self.last_update_time = datetime.now()

#         # 트레이더 동작 콜백 (나중에 주입)
#         self._reduce_position_cb: Optional[Callable[[str, float], Awaitable[None]]] = None
#         self._close_position_cb: Optional[Callable[[str], Awaitable[None]]] = None

#     # 트레이더 포지션 제어 콜백 연결
#     def set_trader_callbacks(
#         self,
#         reduce_position_cb: Callable[[str, float], Awaitable[None]],
#         close_position_cb: Callable[[str], Awaitable[None]],
#     ) -> None:
#         self._reduce_position_cb = reduce_position_cb
#         self._close_position_cb = close_position_cb

#     def get_strategy_params(self) -> Dict[str, Any]:
#         return self.state_strategy_matrix.get(self.current_state, self.state_strategy_matrix["S4"])

#     async def update_state(self) -> bool:
#         """DB에서 최신 시장 상태를 읽고 변경 시 알림/전환 처리"""
#         try:
#             market_data = self.db.get_latest_market_state()
#             if market_data and "state" in market_data:
#                 self.last_state = self.current_state
#                 self.current_state = market_data["state"]
#                 self.state_confidence = market_data.get("confidence", 0.5)
#                 self.last_update_time = datetime.now()

#                 if self.current_state != self.last_state:
#                     self.state_changed_time = datetime.now()
#                     await self.notify_state_change(market_data)
#                     await self.handle_state_transition()
#                 return True
#         except Exception as e:
#             self.logger.error(f"Error updating state: {e}")
#         return False

#     async def handle_state_transition(self) -> None:
#         """상태 전환 시 위험자산 감축/청산 로직"""
#         if not (self._reduce_position_cb and self._close_position_cb):
#             self.logger.debug("Trader callbacks not set; skipping transition actions.")
#             return

#         # S6 -> S7: 고위험 알트 50% 감축
#         if self.last_state == "S6" and self.current_state == "S7":
#             self.logger.info("State transition S6->S7: Reducing high-risk alts by 50%")
#             for symbol in self.symbol_risk_tiers.get("TIER_3", []):
#                 await self._reduce_position_cb(symbol, 0.5)

#         # S7 -> S8: 고위험 알트 전량 청산
#         elif self.last_state == "S7" and self.current_state == "S8":
#             self.logger.info("State transition S7->S8: Closing all high-risk alts")
#             for symbol in self.symbol_risk_tiers.get("TIER_3", []):
#                 await self._close_position_cb(symbol)

#         # S5/S6 -> S0/S1/S2: 긴급 청산
#         elif self.last_state in ["S5", "S6"] and self.current_state in ["S0", "S1", "S2"]:
#             self.logger.warning("Emergency state transition: Closing all alt positions")
#             for tier in ["TIER_3", "TIER_2"]:
#                 for symbol in self.symbol_risk_tiers.get(tier, []):
#                     await self._close_position_cb(symbol)

#     async def notify_state_change(self, market_data: Dict[str, Any]) -> None:
#         """상태 변경 텔레그램 알림 (HTML 포맷)"""
#         try:
#             old_strategy = self.state_strategy_matrix.get(self.last_state, {})
#             new_strategy = self.state_strategy_matrix.get(self.current_state, {})

#             # 안전 이스케이프
#             last_state = html.escape(self.last_state)
#             current_state = html.escape(self.current_state)
#             old_desc = html.escape(old_strategy.get("description", ""))
#             new_desc = html.escape(new_strategy.get("description", ""))
#             amount_mult = html.escape(str(new_strategy.get("amount_multiplier", "")))
#             interval_hours = html.escape(str(new_strategy.get("interval_hours", "")))
#             symbols = ", ".join(html.escape(s) for s in new_strategy.get("allowed_symbols", []))

#             confidence = f"{self.state_confidence:.1%}"
#             btc_price = f"${market_data.get('btc_price', 0):,.0f}"
#             breadth = f"{market_data.get('breadth_above50', 0.5):.1%}"

#             msg = (
#                 "🔄 <b>Market State Changed</b>\n"
#                 "━━━━━━━━━━━━━━━━━━━━\n"
#                 f"From: <code>{last_state}</code> ({old_desc})\n"
#                 f"To: <code>{current_state}</code> ({new_desc})\n"
#                 "━━━━━━━━━━━━━━━━━━━━\n"
#                 f"📊 Confidence: {confidence}\n"
#                 f"💰 BTC Price: {btc_price}\n"
#                 f"📈 Breadth: {breadth}\n"
#                 "━━━━━━━━━━━━━━━━━━━━\n"
#                 f"Amount Multiplier: {amount_mult}\n"
#                 f"Trading Interval: {interval_hours}h\n"
#                 f"Allowed Symbols: {symbols}\n"
#             )

#             if self._send_message:
#                 # _send_message가 parse_mode=HTML로 전송하도록 구현되어 있어야 함
#                 await self._send_message(msg)

#             self.logger.info(f"Market state changed: {self.last_state} -> {self.current_state}")
#         except Exception as e:
#             self.logger.error(f"Error in notify_state_change: {e}")