#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Position Management (Simplified)
- 3-Mode 시스템: STOP/NORMAL/CAUTIOUS
- TP/SL은 YAML 고정값
- PositionAnalyzer는 알림 전용
"""

import time
import logging
from enum import Enum
from datetime import datetime
from typing import Optional, Callable, Awaitable

from adaptive.exchange import OrderManager, BinanceTrader
from adaptive.policies import is_symbol_allowed

logger = logging.getLogger('PosInfo')


class PositionState(Enum):
    NoPos = 0
    HavePos = 1
    MaxPos = 2


class PosInfo:
    def __init__(
        self,
        symbol_config,
        *,
        position_analyzer,
        state_manager,
        send_message: Callable[[str], Awaitable[None]],
        logger: Optional[logging.Logger] = None,
    ):
        self.symbol_config = symbol_config
        self.analyzer = position_analyzer
        self.state_manager = state_manager
        self.send_message = send_message
        self.logger = logger or logging.getLogger('PosInfo')

        self.position_amt = {'LONG': 0, 'SHORT': 0}
        self.entry_price = {'LONG': 0.0, 'SHORT': 100000.0}

        try:
            ticker = BinanceTrader.client.futures_symbol_ticker(
                symbol=symbol_config.symbol_binance
            )
            self.price = float(ticker['price'])
        except Exception:
            self.price = 0.0

        self.leftover = 0
        self.va_target_amount = symbol_config.open_amount
        self.sl_alert_sent = False

        # 히스토리컬 데이터 로드
        self.load_initial_history()

    # ---------------------------
    # 초기 데이터
    # ---------------------------
    def load_initial_history(self):
        """다층 시간대 히스토리컬 데이터 로드"""
        try:
            daily = BinanceTrader.client.futures_klines(
                symbol=self.symbol_config.symbol_binance, interval='1d', limit=200
            )
            four_hour = BinanceTrader.client.futures_klines(
                symbol=self.symbol_config.symbol_binance, interval='4h', limit=180
            )
            hourly = BinanceTrader.client.futures_klines(
                symbol=self.symbol_config.symbol_binance, interval='1h', limit=168
            )

            processed = []
            for k in daily[:-7]:
                processed.append((float(k[4]), float(k[7])))
            for k in four_hour[-42:]:
                processed.append((float(k[4]), float(k[7])))
            for k in hourly:
                processed.append((float(k[4]), float(k[7])))

            for price, volume in processed:
                self.analyzer.update_history(self.symbol_config.symbol, price, volume)

            self.logger.info(
                f"Loaded {len(processed)} data points for {self.symbol_config.symbol}"
            )
        except Exception as e:
            self.logger.error(f"Failed to load historical data for {self.symbol_config.symbol}: {e}")

    # ---------------------------
    # 가격/포지션 업데이트
    # ---------------------------
    def current_price(self) -> float:
        try:
            info = OrderManager.broker.fetch_ticker(self.symbol_config.symbol_binance)
            price = float(info['last'])
            self.price = price
            volume = float(info.get('quoteVolume', 0))
            self.analyzer.update_history(self.symbol_config.symbol, price, volume)
        except Exception:
            price = self.price
            OrderManager.reconnect()
        return price

    def update(self, client=None):
        """Binance 포지션 정보 갱신"""
        try:
            client = client or BinanceTrader.client
            info = client.futures_position_information(
                symbol=self.symbol_config.symbol_binance
            )
            for item in info:
                if item.get('positionSide') == 'BOTH':
                    continue
                self.position_amt[item['positionSide']] = abs(float(item['positionAmt']))
                self.entry_price[item['positionSide']] = float(item['entryPrice'])
        except Exception as e:
            self.logger.error(f"Position update error: {e}")
            BinanceTrader.reconnect()

    # ---------------------------
    # 트레이딩 (단순화)
    # ---------------------------
    async def trade(self):
        """
        3-Mode 기반 트레이딩
        - STOP 모드: 매수 안 함
        - CAUTIOUS 모드: 허용 심볼만, 0.3x 매수
        - NORMAL 모드: YAML 설정 그대로
        """
        cfg = self.symbol_config
        symbol = cfg.symbol

        # 수동 정지 체크
        if cfg.stop_trade:
            return

        # 심볼 허용 여부 체크
        if not is_symbol_allowed(self.state_manager.current_state, symbol):
            # 허용 안 됨 → 기존 포지션 TP/SL만 체크
            if self.position_amt['LONG'] > 0:
                await self._check_tp_sl()
            return

        # 매수 배수 계산
        mode_params = self.state_manager.mode_params
        amount_mult = mode_params['amount_multiplier']

        if amount_mult <= 0:
            # STOP 모드: TP/SL만 체크
            if self.position_amt['LONG'] > 0:
                await self._check_tp_sl()
            return

        # 실제 매수량 = base × multiplier
        effective_amount = cfg.open_amount * amount_mult

        # 전략 실행
        if cfg.is_va:
            await self._trade_va(effective_amount)
        else:
            await self._trade_dca(effective_amount)

    # ---------------------------
    # DCA 전략
    # ---------------------------
    async def _trade_dca(self, effective_amount: float):
        cfg = self.symbol_config
        if cfg.direction == 'LONG':
            await self._trade_long_dca(effective_amount)

    async def _trade_long_dca(self, effective_amount: float):
        cfg = self.symbol_config
        price = self.current_price()
        entry = self.entry_price['LONG']
        pos = self.position_amt['LONG']

        profit_ratio = ((price - entry) / entry * 100) if entry > 0 else 0
        tp = cfg.take_profit_ratio
        sl = cfg.stop_loss_ratio
        current_value = pos * entry if entry > 0 else 0
        max_amount = cfg.max_amount

        self.logger.info(
            f'[{cfg.symbol}] Price: ${price:,.2f}, Profit: {profit_ratio:.2f}%, '
            f'Value: ${current_value:,.0f}, Max: ${max_amount:,.0f}'
        )

        # SL 회복 시 알림 해제
        if self.sl_alert_sent and pos > 0 and profit_ratio > sl + 2:
            self.sl_alert_sent = False

        # 1. 익절
        if pos > 0 and profit_ratio >= tp:
            sell_pos = pos
            if cfg.is_lao and pos > effective_amount * 4:
                sell_pos = max(1, pos / 2)

            OrderManager.sell_market(
                cfg,
                round(price, cfg.price_precision),
                round(sell_pos, cfg.volume_precision),
            )
            msg = f'✅ [{cfg.symbol}][TP] Profit {profit_ratio:.2f}% (target {tp:.1f}%)'
            await self.send_message(msg)
            self.leftover = 0
            return

        # 2. 손절 알림 (자동 매도 안 함)
        if pos > 0 and profit_ratio <= sl:
            await self._send_sl_alert(price, entry, pos, profit_ratio, sl)
            return

        # 3. 신규 매수
        if current_value + effective_amount < max_amount and effective_amount >= 10:
            buy_amount = effective_amount
            if cfg.is_lao and profit_ratio >= 0:
                buy_amount /= 2

            self.leftover += buy_amount
            buy_volume = round(self.leftover / price, cfg.volume_precision)

            if buy_volume > 0:
                OrderManager.buy_market(
                    cfg,
                    round(price, cfg.price_precision),
                    buy_volume,
                )
                msg = (
                    f'[{cfg.symbol}] Buy ${buy_volume * price:,.0f} at ${price:,.2f}, '
                    f'Total ${(pos * price + buy_volume * price):,.0f}'
                )
                await self.send_message(msg)
                self.leftover -= buy_volume * price
            else:
                self.leftover = 0

        elif current_value >= max_amount:
            self.leftover = 0

    # ---------------------------
    # VA 전략
    # ---------------------------
    async def _trade_va(self, effective_amount: float):
        cfg = self.symbol_config
        if cfg.direction == 'LONG':
            await self._trade_long_va(effective_amount)

    async def _trade_long_va(self, effective_amount: float):
        cfg = self.symbol_config
        price = self.current_price()
        pos = self.position_amt['LONG']
        entry = self.entry_price['LONG']

        current_value = pos * price
        if pos <= 0:
            self.va_target_amount = effective_amount

        difference = self.va_target_amount - current_value
        profit_ratio = ((price - entry) / entry * 100) if entry > 0 else 0
        tp = cfg.take_profit_ratio
        sl = cfg.stop_loss_ratio

        self.logger.info(
            f'[VA][{cfg.symbol}] Target: ${self.va_target_amount:,.0f}, '
            f'Current: ${current_value:,.0f}, Diff: ${difference:,.0f}'
        )

        # SL 회복 시 알림 해제
        if self.sl_alert_sent and pos > 0 and profit_ratio > sl + 2:
            self.sl_alert_sent = False

        # 1. 익절
        if pos > 0 and profit_ratio >= tp:
            OrderManager.sell_market(
                cfg,
                round(price, cfg.price_precision),
                round(pos, cfg.volume_precision),
            )
            self.va_target_amount = effective_amount
            msg = f'✅ [{cfg.symbol}][VA][TP] Profit {profit_ratio:.2f}%'
            await self.send_message(msg)
            return

        # 2. 손절 알림
        if pos > 0 and profit_ratio <= sl:
            await self._send_sl_alert(price, entry, pos, profit_ratio, sl)
            return

        # 3. VA 리밸런싱
        if difference > 0 and current_value + difference <= cfg.max_amount:
            buy_volume = round(difference / price, cfg.volume_precision)
            if buy_volume > 0 and abs(difference) > 80:
                OrderManager.buy_market(
                    cfg,
                    round(price, cfg.price_precision),
                    buy_volume,
                )
                msg = f'[{cfg.symbol}][VA] Buy ${difference:,.0f} at ${price:,.2f}'
                await self.send_message(msg)
        elif difference < 0 and abs(difference) > 80:
            sell_volume = min(
                round(-difference / price, cfg.volume_precision),
                pos,
            )
            if sell_volume > 0:
                OrderManager.sell_market(
                    cfg,
                    round(price, cfg.price_precision),
                    sell_volume,
                )
                msg = f'[{cfg.symbol}][VA] Sell ${-difference:,.0f} at ${price:,.2f}'
                await self.send_message(msg)

        # 타겟 증가
        if current_value + difference <= cfg.max_amount:
            self.va_target_amount *= (1 + cfg.increase_rate / 100.0)

    # ---------------------------
    # 공통 유틸
    # ---------------------------
    async def _check_tp_sl(self):
        """STOP/비허용 모드에서도 기존 포지션의 TP/SL은 체크"""
        cfg = self.symbol_config
        price = self.current_price()
        entry = self.entry_price['LONG']
        pos = self.position_amt['LONG']

        if pos <= 0 or entry <= 0:
            return

        profit_ratio = ((price - entry) / entry * 100)

        # 익절
        if profit_ratio >= cfg.take_profit_ratio:
            OrderManager.sell_market(
                cfg,
                round(price, cfg.price_precision),
                round(pos, cfg.volume_precision),
            )
            msg = f'✅ [{cfg.symbol}][TP] Profit {profit_ratio:.2f}% (in STOP mode)'
            await self.send_message(msg)

        # 손절 알림
        elif profit_ratio <= cfg.stop_loss_ratio:
            await self._send_sl_alert(price, entry, pos, profit_ratio, cfg.stop_loss_ratio)

    async def _send_sl_alert(
        self, price: float, entry: float, pos: float,
        profit_ratio: float, sl_ratio: float,
    ):
        """손절 알림 (자동 매도 안 함)"""
        if self.sl_alert_sent:
            return

        cfg = self.symbol_config
        current_value = pos * price
        loss_amount = current_value - (pos * entry)

        msg = (
            f"🚨 <b>STOP LOSS ALERT</b> 🚨\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Symbol: {cfg.symbol}\n"
            f"Price: ${price:,.2f} (Entry: ${entry:,.2f})\n"
            f"Loss: {profit_ratio:.2f}% (Trigger: {sl_ratio:.1f}%)\n"
            f"Value: ${current_value:,.2f} (Loss: ${loss_amount:,.2f})\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚠️ /close_position {cfg.symbol} 또는 /ignore_sl {cfg.symbol}"
        )
        await self.send_message(msg)
        self.sl_alert_sent = True
        self.logger.warning(f"[{cfg.symbol}] SL alert sent at {profit_ratio:.2f}%")