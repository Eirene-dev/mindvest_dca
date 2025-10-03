# adaptive/trader.py
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, Callable, Awaitable, Optional
import html

from adaptive.positions import PosInfo
from adaptive.exchange import OrderManager, BinanceTrader


class EnhancedTrader:
    """
    Refactored EnhancedTrader
    - trade_config(dict[str, AdaptiveSymbolInfo])와 각 종 의존성을 생성자에서 주입
    - TOTAL_PORTFOLIO_LIMIT은 portfolio_limit 인자로 전달
    """

    def __init__(
        self,
        *,
        trade_config: Dict[str, object],
        position_analyzer,
        state_manager,
        send_message: Callable[[str], Awaitable[None]],
        portfolio_limit: float,
        logger: Optional[logging.Logger] = None,
    ):
        self.logger = logger or logging.getLogger("AdaptiveTrader")
        self.state_manager = state_manager
        self.position_analyzer = position_analyzer
        self.send_message = send_message
        self.portfolio_limit = portfolio_limit

        # 심볼별 포지션 객체 생성
        self.pos_info_dict: Dict[str, PosInfo] = {}
        for symbol, cfg in trade_config.items():
            self.pos_info_dict[symbol] = PosInfo(
                cfg,
                position_analyzer=self.position_analyzer,
                state_manager=self.state_manager,
                send_message=self.send_message,
                logger=self.logger,
            )

        self.initial_data_loaded = False

    # ---------------------------
    # 초기 히스토리 데이터 로드
    # ---------------------------
    async def initialize_historical_data(self):
        if self.initial_data_loaded:
            return

        self.logger.info("Loading historical data for all symbols...")
        tasks = [self.load_symbol_history_async(pi) for pi in self.pos_info_dict.values()]
        await asyncio.gather(*tasks)
        self.initial_data_loaded = True
        self.logger.info("Historical data loading completed")

    async def load_symbol_history_async(self, pos_info: PosInfo):
        await asyncio.get_event_loop().run_in_executor(None, pos_info.load_initial_history)

    # ---------------------------
    # 메인 업데이트 루프에서 호출
    # ---------------------------
    async def update_all(self):
        # 시장 상태 갱신
        await self.state_manager.update_state()
        state_params = self.state_manager.get_strategy_params()

        # 익스포저 한도 체크
        await self.check_exposure_limits(state_params)

        # 각 심볼 트레이드 실행
        for symbol, pos in self.pos_info_dict.items():
            
            if pos.symbol_config.stop_trade:
                continue

            # 포지션 정보/가격 업데이트
            pos.update(BinanceTrader.client)
            pos.current_price()

            # 전략 실행
            await pos.trade()

    async def update_symbol(self, symbol: str) -> bool:
        """
        지정한 심볼만 포지션/가격 갱신 후 전략 실행.
        - 전체 상태 업데이트와 익스포저 체크는 그대로 수행
        - stop_trade 인 경우 False 반환
        """
        # 시장 상태 갱신 및 익스포저 체크
        await self.state_manager.update_state()
        state_params = self.state_manager.get_strategy_params()
        await self.check_exposure_limits(state_params)

        pos = self.pos_info_dict.get(symbol)
        if not pos:
            self.logger.warning(f"update_symbol: {symbol} not found")
            return False
        if pos.symbol_config.stop_trade:
            self.logger.info(f"update_symbol: {symbol} is set to stop_trade; skipping")
            return False

        # 포지션/가격 갱신 + 전략 실행
        pos.update(BinanceTrader.client)
        pos.current_price()
        await pos.trade()
        return True
    
    # ---------------------------
    # 리포트/리스크/포지션 관리
    # ---------------------------
    async def get_risk_report(self) -> str:
        lines = ["💰 <b>Risk Management Report</b>", "━━━━━━━━━━━━━━━━━━━━"]
        total_exposure = 0.0
        total_max_exposure = 0.0

        for symbol, pos_info in self.pos_info_dict.items():
            cfg = pos_info.symbol_config
            current_value = pos_info.position_amt['LONG'] * pos_info.price
            total_exposure += current_value
            total_max_exposure += cfg.max_amount

            lines.append(f"\n<b>{html.escape(symbol)}</b>")
            lines.append(f"• Current: ${current_value:,.0f}")
            lines.append(f"• Max Allowed: ${cfg.max_amount:,.0f}")
            lines.append(f"• Base Max: ${cfg.base_max_amount:,.0f}")
            util = (current_value / cfg.max_amount * 100) if cfg.max_amount > 0 else 0
            lines.append(f"• Utilization: {util:.1f}%")

        lines.append("\n<b>Total Portfolio</b>")
        lines.append(f"• Current Exposure: ${total_exposure:,.0f}")
        lines.append(f"• Max Allowed: ${total_max_exposure:,.0f}")
        lines.append(f"• Portfolio Limit: ${self.portfolio_limit:,.0f}")
        lines.append(f"• Utilization: {(total_exposure / self.portfolio_limit * 100):.1f}%")
        return "\n".join(lines)

    async def check_exposure_limits(self, state_params):
        total_exposure = 0.0
        alt_exposure = 0.0

        for symbol, pos_info in self.pos_info_dict.items():
            value = pos_info.position_amt['LONG'] * pos_info.entry_price['LONG']
            total_exposure += value
            if symbol not in ['BTC', 'ETH']:
                alt_exposure += value

        max_total = state_params.get('max_total_exposure', 1.0)
        max_alt = state_params.get('max_alt_exposure', 0.5)

        # BTC가 없다면 첫 심볼의 max_amount 사용
        any_cfg = next(iter(self.pos_info_dict.values())).symbol_config
        btc_cfg = self.pos_info_dict.get('BTC', None)
        base_amount = (btc_cfg.symbol_config if btc_cfg else any_cfg).max_amount

        if total_exposure > base_amount * max_total:
            self.logger.warning(f"Total exposure exceeds limit: ${total_exposure:.0f}")

        if alt_exposure > base_amount * max_alt:
            self.logger.warning(f"Alt exposure exceeds limit: ${alt_exposure:.0f}")

    async def reduce_position(self, symbol: str, ratio: float = 0.5):
        if symbol not in self.pos_info_dict:
            return
        pos_info = self.pos_info_dict[symbol]
        pos = pos_info.position_amt['LONG']
        if pos <= 0:
            return
        sell_amount = pos * ratio
        price = pos_info.current_price()
        OrderManager.sell_market(
            pos_info.symbol_config,
            round(price, pos_info.symbol_config.price_precision),
            round(sell_amount, pos_info.symbol_config.volume_precision),
        )
        self.logger.info(f"Reduced {symbol} position by {ratio:.0%}")

    async def close_position(self, symbol: str):
        await self.reduce_position(symbol, 1.0)

    async def get_position_analysis_report(self) -> str:
        lines = ["📊 <b>Position Analysis Report</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for symbol, position_info in self.pos_info_dict.items():
            score = self.position_analyzer.get_position_score(symbol)

            sym = html.escape(symbol)
            pos_label = html.escape(score["position_label"])

            lines.append(f"\n<b>{sym}</b>")
            lines.append(f"• Position: {pos_label}")
            lines.append(f"• Score: {score['combined_score']:.1f}/100")
            lines.append(f"• RSI: {score['rsi']:.1f}")
            lines.append(f"• Momentum: {score['momentum']:+.1f}%")

            pos = position_info.position_amt["LONG"]
            if pos > 0:
                entry = position_info.entry_price["LONG"]
                current = position_info.price
                profit = ((current - entry) / entry * 100) if entry > 0 else 0
                value = pos * current
                lines.append(f"• Holdings: ${value:,.0f} ({profit:+.1f}%)")
        return "\n".join(lines)
