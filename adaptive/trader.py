#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enhanced Trader (Simplified)
3-Mode 시스템 기반 트레이딩 관리자
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, Callable, Awaitable, Optional
import html

from adaptive.positions import PosInfo
from adaptive.exchange import OrderManager, BinanceTrader
from adaptive.policies import TradingMode


class EnhancedTrader:
    """
    단순화된 트레이더
    - 3-Mode(STOP/NORMAL/CAUTIOUS) 기반
    - 매 사이클: 상태 업데이트 → 심볼별 trade 실행
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
        self.logger = logger or logging.getLogger("EnhancedTrader")
        self.state_manager = state_manager
        self.position_analyzer = position_analyzer
        self.send_message = send_message
        self.portfolio_limit = portfolio_limit

        # 심볼별 PosInfo 생성
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
    # 초기화
    # ---------------------------
    async def initialize_historical_data(self):
        if self.initial_data_loaded:
            return
        self.logger.info("Loading historical data for all symbols...")
        tasks = [
            asyncio.get_event_loop().run_in_executor(
                None, pi.load_initial_history
            )
            for pi in self.pos_info_dict.values()
        ]
        await asyncio.gather(*tasks)
        self.initial_data_loaded = True
        self.logger.info("Historical data loading completed")

    # ---------------------------
    # 메인 업데이트
    # ---------------------------
    async def update_all(self):
        """전체 심볼 업데이트 + 트레이드"""
        await self.state_manager.update_state()

        for symbol, pos in self.pos_info_dict.items():
            if pos.symbol_config.stop_trade:
                continue
            pos.update(BinanceTrader.client)
            pos.current_price()
            await pos.trade()

        # 극단값 알림 (PositionAnalyzer 알림 전용)
        await self._check_position_alerts()

    async def update_symbol(self, symbol: str) -> bool:
        """특정 심볼만 업데이트 + 트레이드"""
        await self.state_manager.update_state()

        pos = self.pos_info_dict.get(symbol)
        if not pos:
            self.logger.warning(f"update_symbol: {symbol} not found")
            return False
        if pos.symbol_config.stop_trade:
            return False

        pos.update(BinanceTrader.client)
        pos.current_price()
        await pos.trade()
        return True

    # ---------------------------
    # 포지션 관리
    # ---------------------------
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

    # ---------------------------
    # 알림 (PositionAnalyzer 활용)
    # ---------------------------
    async def _check_position_alerts(self):
        """극단값일 때만 텔레그램 알림"""
        for symbol in self.pos_info_dict:
            score = self.position_analyzer.get_position_score(symbol)
            combined = score.get('combined_score', 50)

            if combined >= 85:
                msg = (
                    f"⚠️ <b>{symbol} 과매수 경고</b>\n"
                    f"Score: {combined:.1f}/100 ({score['position_label']})\n"
                    f"RSI: {score['rsi']:.1f}"
                )
                await self.send_message(msg)
            elif combined <= 15:
                msg = (
                    f"💰 <b>{symbol} 매수 기회</b>\n"
                    f"Score: {combined:.1f}/100 ({score['position_label']})\n"
                    f"RSI: {score['rsi']:.1f}"
                )
                await self.send_message(msg)

    # ---------------------------
    # 리포트
    # ---------------------------
    async def get_risk_report(self) -> str:
        lines = ["💰 <b>Risk Report</b>", "━━━━━━━━━━━━━━━━━━━━"]
        total_value = 0.0

        mode = self.state_manager.current_mode
        lines.append(f"Mode: <b>{mode.value}</b> (State: {self.state_manager.current_state})")
        lines.append("")

        for symbol, pos_info in self.pos_info_dict.items():
            pos = pos_info.position_amt['LONG']
            if pos <= 0:
                continue
            value = pos * pos_info.price
            entry = pos_info.entry_price['LONG']
            pnl = ((pos_info.price - entry) / entry * 100) if entry > 0 else 0
            total_value += value

            lines.append(f"<b>{html.escape(symbol)}</b>: ${value:,.0f} ({pnl:+.1f}%)")

        lines.append(f"\nTotal: ${total_value:,.0f} / ${self.portfolio_limit:,.0f}")
        return "\n".join(lines)

    async def get_position_analysis_report(self) -> str:
        lines = ["📊 <b>Position Analysis</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for symbol in self.pos_info_dict:
            score = self.position_analyzer.get_position_score(symbol)
            pos_info = self.pos_info_dict[symbol]

            lines.append(f"\n<b>{html.escape(symbol)}</b>")
            lines.append(f"• {score['position_label']} (Score: {score['combined_score']:.1f})")
            lines.append(f"• RSI: {score['rsi']:.1f}, Momentum: {score['momentum']:+.1f}%")

            pos = pos_info.position_amt['LONG']
            if pos > 0:
                entry = pos_info.entry_price['LONG']
                pnl = ((pos_info.price - entry) / entry * 100) if entry > 0 else 0
                lines.append(f"• Holdings: ${pos * pos_info.price:,.0f} ({pnl:+.1f}%)")

        return "\n".join(lines)