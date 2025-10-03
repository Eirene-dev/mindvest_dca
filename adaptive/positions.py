# adaptive/positions.py
import time
import logging
from enum import Enum
from datetime import datetime
from typing import Optional, Callable, Awaitable

from adaptive.exchange import OrderManager, BinanceTrader

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
        self.logger = logger or logging.getLogger('AdaptiveTrader')

        self.last_update_time = time.time() - 3600
        self.position_amt = {'LONG': 0, 'SHORT': 0}
        self.entry_price = {'LONG': 0.0, 'SHORT': 100000.0}
        self.pos_state = {'LONG': PositionState.NoPos, 'SHORT': PositionState.NoPos}

        try:
            ticker = BinanceTrader.client.futures_symbol_ticker(symbol=symbol_config.symbol_binance)
            self.price = float(ticker['price'])
        except Exception:
            self.price = 0.0

        self.open_price = self.price
        self.close_price = self.price
        self.leftover = 0
        self.va_target_amount = symbol_config.open_amount
        self.sl_alert_sent = False

        # 초기 히스토리컬 데이터 로드
        self.load_initial_history()

    def load_initial_history(self):
        """다층 시간대 히스토리컬 데이터 로드"""
        try:
            # 1) 1D
            daily_klines = BinanceTrader.client.futures_klines(
                symbol=self.symbol_config.symbol_binance, interval='1d', limit=200
            )
            # 2) 4H
            four_hour_klines = BinanceTrader.client.futures_klines(
                symbol=self.symbol_config.symbol_binance, interval='4h', limit=180
            )
            # 3) 1H
            hourly_klines = BinanceTrader.client.futures_klines(
                symbol=self.symbol_config.symbol_binance, interval='1h', limit=168
            )

            processed = []
            for k in daily_klines[:-7]:
                processed.append({'price': float(k[4]), 'volume': float(k[7]), 'weight': 0.5})
            for k in four_hour_klines[-42:]:
                processed.append({'price': float(k[4]), 'volume': float(k[7]), 'weight': 0.8})
            for k in hourly_klines:
                processed.append({'price': float(k[4]), 'volume': float(k[7]), 'weight': 1.0})

            for d in processed:
                self.analyzer.update_history(self.symbol_config.symbol, d['price'], d['volume'])

            self.logger.info(f"Loaded {len(processed)} data points for {self.symbol_config.symbol}")
        except Exception as e:
            self.logger.error(f"Failed to load historical data: {e}")

    def current_price(self):
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
        """Binance 포지션 정보 갱신 (client 미전달 시 내부 client 사용)"""
        try:
            client = client or BinanceTrader.client
            info = client.futures_position_information(symbol=self.symbol_config.symbol_binance)
            for item in info:
                if item.get('positionSide') == 'BOTH':
                    continue
                self.position_amt[item['positionSide']] = abs(float(item['positionAmt']))
                self.entry_price[item['positionSide']] = float(item['entryPrice'])
        except Exception as e:
            self.logger.error(e)
            BinanceTrader.reconnect()

    # =========================
    # Trading (원본 로직 유지)
    # =========================
    async def trade(self):
        """적응형 트레이드 (전략은 YAML 설정 따름)"""
        state_params = self.state_manager.get_strategy_params()

        # 자산 위치 분석
        position_score = self.analyzer.get_position_score(self.symbol_config.symbol)

        # 시장 상태 + 자산 위치로 파라미터 조정
        if not self.symbol_config.apply_market_state_and_position(state_params, position_score):
            self.logger.info(f"[{self.symbol_config.symbol}] Trading disabled")
            return

        # 전략 선택
        if self.symbol_config.is_va:
            await self.trade_va()
        else:
            await self.trade_dca()

    async def trade_dca(self):
        if self.symbol_config.direction == 'LONG':
            await self.trade_long()
        elif self.symbol_config.direction == 'SHORT':
            await self.trade_short()

    async def trade_long(self):
        price = self.current_price()
        entry_price = self.entry_price['LONG']
        pos = self.position_amt['LONG']

        profit_ratio = ((price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0
        tp_ratio = self.symbol_config.take_profit_ratio
        sl_ratio = self.symbol_config.stop_loss_ratio
        total_buy_amount = pos * entry_price + self.symbol_config.open_amount if entry_price > 0 else self.symbol_config.open_amount
        max_amount = self.symbol_config.max_amount

        logger.info(f'{datetime.now()} {self.symbol_config.symbol}, Profit: {profit_ratio:0,.2f}%, Target: {tp_ratio:0,.2f}%, Buy: {total_buy_amount:0,.2f}, Max: ${max_amount}, Reduce: {self.symbol_config.reduce_only}')

        # 손절선 회복 시 알림 플래그 해제
        if self.sl_alert_sent and pos > 0 and profit_ratio > sl_ratio + 2:
            self.sl_alert_sent = False
        
        # 익절
        if pos > 0 and profit_ratio >= tp_ratio:
            pos_to_sell = pos
            if self.symbol_config.is_lao and pos > self.symbol_config.open_amount * 4:
                pos_to_sell = max(1, pos / 2)
            OrderManager.sell_market(self.symbol_config, round(price, self.symbol_config.price_precision), round(pos_to_sell, self.symbol_config.volume_precision))
            msg = '**[{}][TP] Close Long: Profit {:0,.2f}% ({:0,.2f}%)'.format(self.symbol_config.symbol, profit_ratio, tp_ratio)
            await self.send_message(msg)
            self.leftover = 0

        # 손절 알림
        elif pos > 0 and profit_ratio <= sl_ratio:
            if not hasattr(self, 'sl_alert_sent') or not self.sl_alert_sent:
                current_value = pos * price
                loss_amount = current_value - (pos * entry_price)
                msg = f"""
🚨 **STOP LOSS ALERT** 🚨
━━━━━━━━━━━━━━━━
Symbol: {self.symbol_config.symbol}
Current Price: ${price:,.2f}
Entry Price: ${entry_price:,.2f}
Loss: {profit_ratio:.2f}% (Trigger: {sl_ratio:.2f}%)
Position Value: ${current_value:,.2f}
Loss Amount: ${loss_amount:,.2f}
━━━━━━━━━━━━━━━━
⚠️ **Manual action required!**
/close_position {self.symbol_config.symbol} 또는 /ignore_sl {self.symbol_config.symbol}
"""
                await self.send_message(msg)
                self.sl_alert_sent = True
                self.logger.warning(f"[{self.symbol_config.symbol}] Stop loss triggered but not executed - Alert sent")

        # 신규 매수
        elif total_buy_amount < max_amount and not self.symbol_config.reduce_only:
            buy_amount = self.symbol_config.open_amount
            
            if buy_amount > 0:
                if self.symbol_config.is_lao and profit_ratio >= 0:
                    buy_amount /= 2
                self.leftover += buy_amount
                buy_volume = round(self.leftover / price, self.symbol_config.volume_precision)

                if buy_volume > 0:
                    OrderManager.buy_market(self.symbol_config, round(price, self.symbol_config.price_precision), buy_volume)
                    msg = '[{}] Open Price {}, Qty ${:0,.0f}+{:0,.0f}, Profit {:0,.2f}%'.format(
                        self.symbol_config.symbol, price, buy_volume * price, self.position_amt['LONG'] * price, profit_ratio)
                    await self.send_message(msg)

                self.leftover -= buy_volume * price if buy_volume > 0 else 0

        else:
            if self.symbol_config.reduce_only and pos == 0:
                return
            self.leftover = 0
            if total_buy_amount >= max_amount:
                msg = '**[{}][Max] Current ${:0,.2f} (Max ${:0,.2f})'.format(
                    self.symbol_config.symbol, pos * entry_price if entry_price > 0 else 0, max_amount)
                await self.send_message(msg)

    async def trade_short(self):
        """DCA Short (미구현)"""
        pass

    async def trade_va(self):
        if self.symbol_config.direction == 'LONG':
            await self.trade_long_va()
        elif self.symbol_config.direction == 'SHORT':
            await self.trade_short_va()

    async def trade_long_va(self):
        price = self.current_price()
        pos = self.position_amt['LONG']
        entry_price = self.entry_price['LONG']

        current_asset_value = pos * price
        if pos <= 0:
            self.va_target_amount = self.symbol_config.open_amount
        difference = self.va_target_amount - current_asset_value
        profit_ratio = ((price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0

        # 익절/손절
        if pos > 0 and (profit_ratio >= self.symbol_config.take_profit_ratio or profit_ratio <= self.symbol_config.stop_loss_ratio):
            OrderManager.sell_market(self.symbol_config, round(price, self.symbol_config.price_precision), round(pos, self.symbol_config.volume_precision))
            self.va_target_amount = self.symbol_config.open_amount
            action = 'TP' if profit_ratio >= self.symbol_config.take_profit_ratio else 'SL'
            msg = f'**[{self.symbol_config.symbol}][{action}] VA Close: Profit {profit_ratio:.2f}%'
            await self.send_message(msg)
            return

        if self.symbol_config.reduce_only:
            return

        # VA 리밸런싱
        if difference > 0 and current_asset_value + difference <= self.symbol_config.max_amount:
            buy_volume = round(difference / price, self.symbol_config.volume_precision)
            if buy_volume > 0 and difference > 100:
                OrderManager.buy_market(self.symbol_config, round(price, self.symbol_config.price_precision), buy_volume)
                msg = f'[{self.symbol_config.symbol}] VA Buy: ${difference:.0f} at {price}'
                await self.send_message(msg)
        elif difference < 0 and difference > 100:
            sell_volume = round(-difference / price, self.symbol_config.volume_precision)
            sell_volume = min(sell_volume, pos)
            if sell_volume > 0:
                OrderManager.sell_market(self.symbol_config, round(price, self.symbol_config.price_precision), sell_volume)
                msg = f'[{self.symbol_config.symbol}] VA Sell: ${-difference:.0f} at {price}'
                await self.send_message(msg)

        # 타겟 증가
        self.va_target_amount *= (1 + self.symbol_config.increase_rate / 100.0)

    async def trade_short_va(self):
        """VA Short (미구현)"""
        pass
