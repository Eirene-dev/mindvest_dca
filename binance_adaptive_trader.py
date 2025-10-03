#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enhanced Market State-Aware Crypto Trading Bot with Dynamic Risk Management
시장 상태와 개별 자산 위치에 따라 리스크 한도를 동적으로 조정하는 트레이딩 봇
"""

import os
import sys
import yaml
import time
import asyncio
import logging
import threading
from dotenv import load_dotenv
from enum import Enum
from datetime import datetime, timedelta

# Trading Libraries
from binance.client import Client
import ccxt

# Telegram
from telegram import ForceReply, Update
from telegram.ext import filters, CallbackContext, CommandHandler, ApplicationBuilder, ContextTypes, MessageHandler

# Plotting
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# My modules
from adaptive.position_analyzer import PositionAnalyzer
from adaptive.db_manager import DatabaseManager
from adaptive.commands import Services, Commands, register as register_bot_commands
from adaptive.market_state import MarketStateManager
from adaptive.policies import (
    SYMBOL_RISK_TIERS,
    TIER_MAX_MULTIPLIERS,
    STATE_STRATEGY_MATRIX,
    POSITION_ADJUSTMENT_MATRIX,
    get_state_params,   # 선택: 사용하면 더 안전
    get_adjustments,    # 선택: 필요시 사용
)
from adaptive.positions import PosInfo, PositionState
from adaptive.exchange import OrderManager, BinanceTrader


# ==========================================
# Configuration
# ==========================================
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'xxx')
CHAT_ID = int(os.getenv('CHAT_ID', '122'))

API_KEY = os.getenv('BINANCE_API_KEY', 'xxx')
API_SEC = os.getenv('BINANCE_API_SECRET', 'xxx')
BinanceTrader.init(API_KEY, API_SEC)
OrderManager.init(API_KEY, API_SEC)

# Portfolio Settings
TOTAL_PORTFOLIO_LIMIT = float(os.getenv('TOTAL_PORTFOLIO_LIMIT', '100000'))  # 전체 포트폴리오 한도


print(f"Total Portfolio Limit: ${TOTAL_PORTFOLIO_LIMIT:,.0f}")

# Telegram Application
application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

# Logger setup
logger = logging.getLogger('AdaptiveTrader')
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(name)s;%(asctime)s;%(levelname)s;%(message)s')

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

file_handler = logging.FileHandler('adaptive_trader.log')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# ==========================================
# Telegram Manager
# ==========================================

class TelegramManager:
    @staticmethod
    async def send_message(msg):
        global application
        try:
            # HTML 파싱으로 변경
            msg = msg.replace('**', '<b>').replace('**', '</b>')
            msg = msg.replace('*', '<i>').replace('*', '</i>')
            await application.bot.send_message(chat_id=CHAT_ID, text=msg)
        except Exception as e:
            # 파싱 실패시 plain text로 재시도
            try:
                await application.bot.send_message(chat_id=CHAT_ID, text=msg)
            except:
                logger.error(f"Telegram send error: {e}")


# ==========================================
# Adaptive Analyzers
# ==========================================
position_analyzer = PositionAnalyzer(max_history_length=600)
db_manager = DatabaseManager()

state_manager = MarketStateManager(
    db_manager=db_manager,
    symbol_risk_tiers=SYMBOL_RISK_TIERS,
    state_strategy_matrix=STATE_STRATEGY_MATRIX,
    send_message=TelegramManager.send_message,
    logger=logger,
)

# ==========================================
# Configuration Classes
# ==========================================

class ConfigLoader:
    def __init__(self):
        pass

    def load(self, file_path):
        if not os.path.exists(file_path):
            print('File {} does not exist.'.format(file_path))
            sys.exit(1)

        with open(file_path) as f:
            conf = yaml.load(f, Loader=yaml.FullLoader)

            self.symbol = conf['symbol']
            self.price_precision = conf['price_precision']
            self.volume_precision = conf['volume_precision']
            self.open_amount = conf['open_amount']
            self.max_amount = conf['max_amount']
            self.direction = conf['direction']
            self.take_profit_ratio = conf['take_profit_ratio']
            self.stop_loss_ratio = conf['stop_loss_ratio']
            self.is_lao = conf.get('is_lao', False)
            self.is_va = conf.get('is_va', False)
            self.increase_rate = conf.get('increase_rate', 1)

    def print_config(self):
        print('-- [ Config ] ----------------------------------------------------------------')
        print(f'Symbol {self.symbol}, Volume {self.open_amount}, Max {self.max_amount}, '
              f'Strategy: {"VA" if self.is_va else "DCA"}, LAO {self.is_lao}')
        print('------------------------------------------------------------------------------\n')

class SymbolInfo:
    def __init__(self, config_loader):
        self.symbol = config_loader.symbol
        self.symbol_binance = '{}USDT'.format(config_loader.symbol)
        self.symbol_binance_order = '{}/USDT'.format(config_loader.symbol)
        self.price_precision = config_loader.price_precision
        self.volume_precision = config_loader.volume_precision
        self.open_amount = config_loader.open_amount
        self.max_amount = config_loader.max_amount
        self.direction = config_loader.direction
        self.take_profit_ratio = config_loader.take_profit_ratio
        self.stop_loss_ratio = config_loader.stop_loss_ratio
        self.is_lao = config_loader.is_lao
        self.is_va = config_loader.is_va
        self.increase_rate = config_loader.increase_rate
        self.leftover = 0
        self.stop_trade = False

    def __str__(self):
        return 'Symbol {}, Amount {:,}, Max {}, Strategy {}, TP {}%, SL {}%'.format(
            self.symbol, self.open_amount, self.max_amount,
            'VA' if self.is_va else 'DCA',
            self.take_profit_ratio, self.stop_loss_ratio)

class AdaptiveSymbolInfo(SymbolInfo):
    """시장 상태와 자산 위치를 모두 고려하는 심볼 정보 (max_amount 동적 조정 포함)"""
    
    def __init__(self, config_loader):
        super().__init__(config_loader)
        self.base_open_amount = self.open_amount
        self.base_max_amount = self.max_amount  # 기본 최대 금액 저장
        self.base_tp_ratio = self.take_profit_ratio
        self.base_sl_ratio = self.stop_loss_ratio
        self.reduce_only = False
        self.position_score = None
        
    def apply_market_state_and_position(self, state_params, position_score):
        """시장 상태와 자산 위치를 모두 고려한 파라미터 조정 (max_amount 포함)"""
        
        # 사용자 수동 Stop 우선
        if self.stop_trade:
            return False
        
        # 허용 심볼 체크
        if self.symbol not in state_params['allowed_symbols']:
            self.reduce_only = True
            self.take_profit_ratio = self.base_tp_ratio
            self.stop_loss_ratio = self.base_sl_ratio
            self.open_amount = 0
            self.max_amount = 0  # 허용되지 않은 심볼은 max_amount도 0
            logger.info(f"[{self.symbol}] Not allowed - reduce only mode")
            return True
            
        self.reduce_only = False
        self.position_score = position_score
        
        # 시장 상태가 비활성화면 중단
        if not state_params['enabled']:
            self.stop_trade = True
            return False
        
        self.stop_trade = False
        
        # 1. 시장 상태별 기본 조정
        symbol_weight = state_params.get('symbol_weights', {}).get(self.symbol, 1.0)
        base_amount = self.base_open_amount * state_params['amount_multiplier'] * symbol_weight
        base_max = self.base_max_amount * state_params.get('max_amount_multiplier', 0.5)
        base_tp = state_params['tp_ratio']
        base_sl = state_params['sl_ratio']
        
        # 2. 리스크 티어별 조정
        symbol_tier = None
        for tier, symbols in SYMBOL_RISK_TIERS.items():
            if self.symbol in symbols:
                symbol_tier = tier
                break
        
        tier_max_multiplier = TIER_MAX_MULTIPLIERS.get(symbol_tier, 0.5)
        base_max *= tier_max_multiplier
        
        # 3. 2차원 매트릭스에서 조정값 가져오기
        position_label = position_score.get('position_label', 'NEUTRAL')
        matrix_key = (state_manager.current_state, position_label)
        
        if matrix_key in POSITION_ADJUSTMENT_MATRIX:
            adjustments = POSITION_ADJUSTMENT_MATRIX[matrix_key]
        else:
            adjustments = POSITION_ADJUSTMENT_MATRIX['DEFAULT']
        
        # 4. 최종 파라미터 계산
        self.open_amount = base_amount * adjustments['amount_mult']
        self.max_amount = base_max * adjustments['max_mult']
        self.take_profit_ratio = base_tp * adjustments['tp_mult']
        self.stop_loss_ratio = base_sl * adjustments['sl_mult']
        
        # 5. 안전 제약 적용
        # max_amount는 base의 150%를 초과할 수 없음
        self.max_amount = min(self.base_max_amount * 1.5, self.max_amount)
        
        # open_amount는 max_amount의 20%를 초과할 수 없음
        if self.open_amount > self.max_amount * 0.2:
            self.open_amount = self.max_amount * 0.1
        
        # TP/SL 극단값 제한
        self.take_profit_ratio = max(1, min(30, self.take_profit_ratio))
        self.stop_loss_ratio = max(-30, min(-1, self.stop_loss_ratio))
        
        # 최소 거래량 체크
        if self.open_amount < 10:  # $10 미만은 거래 안함
            self.open_amount = 0
        
        logger.info(f"[{self.symbol}] State: {state_manager.current_state}, "
                   f"Position: {position_label} ({position_score.get('combined_score', 50):.1f}), "
                   f"Amount: ${self.open_amount:.0f}, Max: ${self.max_amount:.0f}, "
                   f"TP: {self.take_profit_ratio:.1f}%, SL: {self.stop_loss_ratio:.1f}%")
        
        return True

# ==========================================
# Trading Classes
# ==========================================

class TradeConfig:
    trade_config = dict()
    current_symbol = ''
    thread_lock = threading.Lock()
    thread_lock_volume = threading.Lock()
    thread_lock_pos = threading.Lock()
    range_min = 60 * 60 * 4
    binance_api_key = API_KEY
    binance_api_sec = API_SEC
    
    @classmethod
    def loads(cls):
        for i in range(1, len(sys.argv)):
            print('Loads {}'.format(sys.argv[i]))
            config_loader = ConfigLoader()
            config_loader.load(sys.argv[i])
            config_loader.print_config()
            cls.trade_config[config_loader.symbol] = AdaptiveSymbolInfo(config_loader)
            cls.current_symbol = config_loader.symbol
        
        # 전체 포트폴리오 한도 체크 및 조정
        cls.check_portfolio_limits()
    
    @classmethod
    def check_portfolio_limits(cls):
        """전체 포트폴리오 한도 체크 및 비율 조정"""
        total_max = sum(cfg.max_amount for cfg in cls.trade_config.values())
        
        if total_max > TOTAL_PORTFOLIO_LIMIT:
            scale = TOTAL_PORTFOLIO_LIMIT / total_max
            logger.warning(f"Total max amounts (${total_max:.0f}) exceed portfolio limit "
                          f"(${TOTAL_PORTFOLIO_LIMIT:.0f}). Scaling down by {scale:.2%}")
            
            for cfg in cls.trade_config.values():
                cfg.base_max_amount *= scale
                cfg.max_amount *= scale
                cfg.base_open_amount *= scale
                cfg.open_amount *= scale
    
    @classmethod
    def current_symbol_config(cls):
        return cls.trade_config.get(cls.current_symbol)

# YAML 파일 로드를 메인 실행 전에 처리
if len(sys.argv) > 1:
    TradeConfig.loads()

# def reconnect():
#     return ccxt.binance(config={
#         'apiKey': TradeConfig.binance_api_key,
#         'secret': TradeConfig.binance_api_sec,
#         'enableRateLimit': True,
#         'options': {'defaultType': 'future'}
#     })

# class OrderManager:
#     broker = reconnect()

#     @classmethod
#     def reconnect(cls):
#         cls.broker = reconnect()

#     @classmethod
#     def buy_market(cls, symbol_config, request_price, vol):
#         if symbol_config.direction == 'LONG':
#             BinanceTrader.open_long(symbol_config, request_price, vol, False)
#         elif symbol_config.direction == 'SHORT':
#             BinanceTrader.open_short(symbol_config, request_price, vol, False)

#     @classmethod
#     def sell_market(cls, symbol_config, request_price, vol):
#         if symbol_config.direction == 'LONG':
#             BinanceTrader.close_long(symbol_config, request_price, vol, False)
#         elif symbol_config.direction == 'SHORT':
#             BinanceTrader.close_short(symbol_config, request_price, vol, False)

# class BinanceTrader:
#     client = Client(api_key=TradeConfig.binance_api_key, api_secret=TradeConfig.binance_api_sec)

#     @classmethod
#     def reconnect(cls):
#         cls.client = Client(api_key=TradeConfig.binance_api_key, api_secret=TradeConfig.binance_api_sec)

#     @classmethod
#     def open_long(cls, symbol_config, price, quantity, is_limit):
#         cls.create_order(symbol_config, price, 'BUY', 'LONG', quantity, is_limit)

#     @classmethod
#     def close_long(cls, symbol_config, price, quantity, is_limit):
#         cls.create_order(symbol_config, price, 'SELL', 'LONG', quantity, is_limit)

#     @classmethod
#     def open_short(cls, symbol_config, price, quantity, is_limit):
#         cls.create_order(symbol_config, price, 'SELL', 'SHORT', quantity, is_limit)

#     @classmethod
#     def close_short(cls, symbol_config, price, quantity, is_limit):
#         cls.create_order(symbol_config, price, 'BUY', 'SHORT', quantity, is_limit)

#     @classmethod
#     def create_order(cls, symbol_config, price, side, position_side, quantity, is_limit):
#         price = round(price, symbol_config.price_precision)
#         try:
#             if is_limit:
#                 order = cls.client.futures_create_order(
#                     symbol=symbol_config.symbol_binance,
#                     type='LIMIT',
#                     timeInForce='GTC',
#                     price=price,
#                     side=side,
#                     positionSide=position_side,
#                     quantity=quantity
#                 )
#             else:
#                 order = cls.client.futures_create_order(
#                     symbol=symbol_config.symbol_binance,
#                     type='MARKET',
#                     side=side,
#                     positionSide=position_side,
#                     quantity=quantity
#                 )
#             logger.info(order)
#         except Exception as e:
#             logger.error('{} {} {} {} {}'.format(symbol_config.symbol, price, position_side, quantity, str(e)))

# class PositionState(Enum):
#     NoPos = 0
#     HavePos = 1
#     MaxPos = 2

# class PosInfo:
#     def __init__(self, symbol_config):
#         self.symbol_config = symbol_config
#         self.last_update_time = time.time() - 3600
#         self.position_amt = {'LONG':0, 'SHORT':0}
#         self.entry_price = {'LONG':0.0, 'SHORT':100000.0}
#         self.pos_state = {'LONG':PositionState.NoPos, 'SHORT':PositionState.NoPos}
        
#         try:
#             ticker = BinanceTrader.client.futures_symbol_ticker(symbol=symbol_config.symbol_binance)
#             self.price = float(ticker['price'])
#         except:
#             self.price = 0
            
#         self.open_price = self.price
#         self.close_price = self.price
#         self.leftover = 0
#         self.va_target_amount = symbol_config.open_amount

#         # 초기 히스토리컬 데이터 로드
#         self.load_initial_history()

#     def load_initial_history(self):
#         """다층 시간대 히스토리컬 데이터 로드"""
#         try:
#             # 1. 장기 트렌드 (1D - 200일)
#             daily_klines = BinanceTrader.client.futures_klines(
#                 symbol=self.symbol_config.symbol_binance,
#                 interval='1d',
#                 limit=200
#             )
            
#             # 2. 중기 패턴 (4H - 최근 30일)
#             four_hour_klines = BinanceTrader.client.futures_klines(
#                 symbol=self.symbol_config.symbol_binance,
#                 interval='4h',
#                 limit=180  # 30일
#             )
            
#             # 3. 단기 정밀도 (1H - 최근 7일)
#             hourly_klines = BinanceTrader.client.futures_klines(
#                 symbol=self.symbol_config.symbol_binance,
#                 interval='1h',
#                 limit=168  # 7일
#             )
            
#             # 데이터 통합 (오래된 것부터)
#             processed_data = []
            
#             # 일봉 데이터 (200일 전 ~ 7일 전)
#             for kline in daily_klines[:-7]:
#                 processed_data.append({
#                     'price': float(kline[4]),
#                     'volume': float(kline[7]),
#                     'weight': 0.5  # 오래된 데이터는 낮은 가중치
#                 })
            
#             # 4시간봉 데이터 (최근 30일)
#             for kline in four_hour_klines[-42:]:  # 최근 7일분
#                 processed_data.append({
#                     'price': float(kline[4]),
#                     'volume': float(kline[7]),
#                     'weight': 0.8
#                 })
            
#             # 1시간봉 데이터 (최근 7일)
#             for kline in hourly_klines:
#                 processed_data.append({
#                     'price': float(kline[4]),
#                     'volume': float(kline[7]),
#                     'weight': 1.0  # 최신 데이터는 높은 가중치
#                 })
            
#             # Position Analyzer에 로드
#             for data in processed_data:
#                 position_analyzer.update_history(
#                     self.symbol_config.symbol,
#                     data['price'],
#                     data['volume']
#                 )
            
#             logger.info(f"Loaded {len(processed_data)} data points for {self.symbol_config.symbol}")
            
#         except Exception as e:
#             logger.error(f"Failed to load historical data: {e}")

#     def current_price(self):
#         try:
#             info = OrderManager.broker.fetch_ticker(self.symbol_config.symbol_binance)
#             price = float(info['last'])
#             self.price = price
            
#             # Position Analyzer에 가격 업데이트
#             volume = float(info.get('quoteVolume', 0))
#             position_analyzer.update_history(self.symbol_config.symbol, price, volume)
            
#         except Exception:
#             price = self.price
#             OrderManager.reconnect()
#         return price

#     def update(self, client):
#         try:
#             info = client.futures_position_information(symbol=self.symbol_config.symbol_binance)
#             for item in info:
#                 if item['positionSide'] == 'BOTH':
#                     continue
#                 self.position_amt[item['positionSide']] = abs(float(item['positionAmt']))
#                 self.entry_price[item['positionSide']] = float(item['entryPrice'])
#         except Exception as e:
#             logger.error(e)
#             BinanceTrader.reconnect()

#     async def trade(self):
#         """적응형 트레이드 (전략은 YAML 설정 따름)"""
#         state_params = state_manager.get_strategy_params()
        
#         # 자산 위치 분석
#         position_score = position_analyzer.get_position_score(self.symbol_config.symbol)
        
#         # 시장 상태와 자산 위치 모두 고려하여 파라미터 조정
#         if not self.symbol_config.apply_market_state_and_position(state_params, position_score):
#             logger.info(f"[{self.symbol_config.symbol}] Trading disabled")
#             return
        
#         # 전략 선택은 YAML의 is_va 설정을 따름
#         if self.symbol_config.is_va:
#             await self.trade_va()
#         else:
#             await self.trade_dca()

#     async def trade_dca(self):
#         """DCA 전략 실행"""
#         if self.symbol_config.direction == 'LONG':
#             await self.trade_long()
#         elif self.symbol_config.direction == 'SHORT':
#             await self.trade_short()

#     async def trade_long(self):
#         """DCA Long 트레이딩"""
#         price = self.current_price()
#         entry_price = self.entry_price['LONG']
#         pos = self.position_amt['LONG']
        
#         profit_ratio = ((price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0
#         tp_ratio = self.symbol_config.take_profit_ratio
#         sl_ratio = self.symbol_config.stop_loss_ratio
#         total_buy_amount = pos * entry_price + self.symbol_config.open_amount if entry_price > 0 else self.symbol_config.open_amount
#         max_amount = self.symbol_config.max_amount

#         print('{} {} Profit: {:0,.2f}%, Target: {:0,.2f}%'.format(
#             str(datetime.now()), self.symbol_config.symbol, profit_ratio, tp_ratio))

#         # 익절
#         if pos > 0 and profit_ratio >= tp_ratio:
#             pos_to_sell = pos
#             if self.symbol_config.is_lao and pos > self.symbol_config.open_amount*4:
#                 pos_to_sell = max(1, pos / 2)
#             OrderManager.sell_market(
#                 self.symbol_config, 
#                 round(price, self.symbol_config.price_precision), 
#                 round(pos_to_sell, self.symbol_config.volume_precision)
#             )
#             msg = '**[{}][TP] Close Long: Profit {:0,.2f}% ({:0,.2f}%)'.format(
#                 self.symbol_config.symbol, profit_ratio, tp_ratio)
#             await TelegramManager.send_message(msg)
#             self.leftover = 0

#         # 손절(자동 손절)
#         # elif pos > 0 and profit_ratio <= sl_ratio:
#         #     pos_to_sell = pos
#         #     OrderManager.sell_market(
#         #         self.symbol_config, 
#         #         round(price, self.symbol_config.price_precision), 
#         #         round(pos_to_sell, self.symbol_config.volume_precision)
#         #     )
#         #     msg = '**[{}][SL] Close Long: Profit {:0,.2f}% ({:0,.2f}%)'.format(
#         #         self.symbol_config.symbol, profit_ratio, sl_ratio)
#         #     await TelegramManager.send_message(msg)
#         #     self.leftover = 0
#         # 손절 알림만 (자동 실행 제거)
#         elif pos > 0 and profit_ratio <= sl_ratio:
#             # 손절 알림 플래그 설정 (중복 알림 방지)
#             if not hasattr(self, 'sl_alert_sent') or not self.sl_alert_sent:
#                 current_value = pos * price
#                 loss_amount = current_value - (pos * entry_price)
                
#                 msg = f"""
#     🚨 **STOP LOSS ALERT** 🚨
#     ━━━━━━━━━━━━━━━━━━━━
#     Symbol: {self.symbol_config.symbol}
#     Current Price: ${price:,.2f}
#     Entry Price: ${entry_price:,.2f}
#     Loss: {profit_ratio:.2f}% (Trigger: {sl_ratio:.2f}%)
#     Position Value: ${current_value:,.2f}
#     Loss Amount: ${loss_amount:,.2f}
#     ━━━━━━━━━━━━━━━━━━━━
#     ⚠️ **Manual action required!**
#     Use /close_position {self.symbol_config.symbol} to close
#     or /ignore_sl {self.symbol_config.symbol} to ignore
#                 """
#                 await TelegramManager.send_message(msg)
#                 self.sl_alert_sent = True
#                 logger.warning(f"[{self.symbol_config.symbol}] Stop loss triggered but not executed - Alert sent")
        
#         # 손절선 회복 시 플래그 리셋
#         elif pos > 0 and profit_ratio > sl_ratio + 2:  # 손절선보다 2% 위로 회복
#             self.sl_alert_sent = False

#         # 신규 매수 (reduce_only가 False일 때만)
#         elif total_buy_amount < max_amount and not self.symbol_config.reduce_only:
#             buy_amount = self.symbol_config.open_amount
#             if buy_amount > 0:
#                 if self.symbol_config.is_lao and profit_ratio >= 0:
#                     buy_amount /= 2
#                 self.leftover += buy_amount
#                 buy_volume = round(self.leftover / price, self.symbol_config.volume_precision)

#                 if buy_volume > 0:
#                     OrderManager.buy_market(
#                         self.symbol_config, 
#                         round(price, self.symbol_config.price_precision), 
#                         buy_volume
#                     )
#                     msg = '[{}] Open Price {}, Qty ${:0,.0f}+{:0,.0f}, Profit {:0,.2f}%'.format(
#                         self.symbol_config.symbol, price, buy_volume*price, 
#                         self.position_amt['LONG']*price, profit_ratio)
#                     await TelegramManager.send_message(msg)
                
#                 self.leftover -= buy_volume * price if buy_volume > 0 else 0
#         else:
#             if self.symbol_config.reduce_only and pos == 0:
#                 return
#             self.leftover = 0
#             if total_buy_amount >= max_amount:
#                 msg = '**[{}][Max] Current ${:0,.2f} (Max ${:0,.2f})'.format(
#                     self.symbol_config.symbol, pos * entry_price if entry_price > 0 else 0, max_amount)
#                 await TelegramManager.send_message(msg)

#     async def trade_short(self):
#         """DCA Short 트레이딩 (구현 필요)"""
#         pass

#     async def trade_va(self):
#         """VA 전략 실행"""
#         if self.symbol_config.direction == 'LONG':
#             await self.trade_long_va()
#         elif self.symbol_config.direction == 'SHORT':
#             await self.trade_short_va()

#     async def trade_long_va(self):
#         """VA Long 트레이딩"""
#         price = self.current_price()
#         pos = self.position_amt['LONG']
#         entry_price = self.entry_price['LONG']
        
#         current_asset_value = pos * price
#         difference = self.va_target_amount - current_asset_value
#         profit_ratio = ((price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0
        
#         # 익절/손절
#         if pos > 0:
#             if profit_ratio >= self.symbol_config.take_profit_ratio or \
#                profit_ratio <= self.symbol_config.stop_loss_ratio:
#                 OrderManager.sell_market(
#                     self.symbol_config,
#                     round(price, self.symbol_config.price_precision),
#                     round(pos, self.symbol_config.volume_precision)
#                 )
#                 self.va_target_amount = self.symbol_config.open_amount
                
#                 action = 'TP' if profit_ratio >= self.symbol_config.take_profit_ratio else 'SL'
#                 msg = f'**[{self.symbol_config.symbol}][{action}] VA Close: Profit {profit_ratio:.2f}%'
#                 await TelegramManager.send_message(msg)
#                 return
        
#         # reduce_only 체크
#         if self.symbol_config.reduce_only:
#             return
        
#         # VA 리밸런싱
#         if difference > 0 and current_asset_value + difference <= self.symbol_config.max_amount:
#             buy_volume = round(difference / price, self.symbol_config.volume_precision)
#             if buy_volume > 0 and difference > 100:
#                 OrderManager.buy_market(
#                     self.symbol_config,
#                     round(price, self.symbol_config.price_precision),
#                     buy_volume
#                 )
#                 msg = f'[{self.symbol_config.symbol}] VA Buy: ${difference:.0f} at {price}'
#                 await TelegramManager.send_message(msg)
#         elif difference < 0 and difference > 100:
#             sell_volume = round(-difference / price, self.symbol_config.volume_precision)
#             sell_volume = min(sell_volume, pos)
#             if sell_volume > 0:
#                 OrderManager.sell_market(
#                     self.symbol_config,
#                     round(price, self.symbol_config.price_precision),
#                     sell_volume
#                 )
#                 msg = f'[{self.symbol_config.symbol}] VA Sell: ${-difference:.0f} at {price}'
#                 await TelegramManager.send_message(msg)
        
#         # 타겟 증가
#         self.va_target_amount *= (1 + self.symbol_config.increase_rate/100.0)

#     async def trade_short_va(self):
#         """VA Short 트레이딩 (구현 필요)"""
#         pass

# ==========================================
# Enhanced Trader
# ==========================================

class EnhancedTrader:
    # def __init__(self):
    #     self.pos_info_dict = dict()
    #     for symbol in TradeConfig.trade_config:
    #         self.pos_info_dict[symbol] = PosInfo(TradeConfig.trade_config[symbol])

    #     # 비동기로 초기 데이터 로드
    #     self.initial_data_loaded = False
    def __init__(self):
        self.pos_info_dict = dict()
        for symbol, cfg in TradeConfig.trade_config.items():
            self.pos_info_dict[symbol] = PosInfo(
                cfg,
                position_analyzer=position_analyzer,
                state_manager=state_manager,
                send_message=TelegramManager.send_message,
                logger=logger,
            )
        self.initial_data_loaded = False

    async def initialize_historical_data(self):
        """비동기 초기 데이터 로드"""
        if self.initial_data_loaded:
            return
            
        logger.info("Loading historical data for all symbols...")
        
        tasks = []
        for symbol, pos_info in self.pos_info_dict.items():
            # 각 심볼을 병렬로 로드
            tasks.append(self.load_symbol_history_async(pos_info))
        
        await asyncio.gather(*tasks)
        self.initial_data_loaded = True
        logger.info("Historical data loading completed")
    
    async def load_symbol_history_async(self, pos_info):
        """비동기 심볼 히스토리 로드"""
        await asyncio.get_event_loop().run_in_executor(
            None, 
            pos_info.load_initial_history
        )

    async def update_all(self):
        """모든 심볼 업데이트"""
        await state_manager.update_state()
        state_params = state_manager.get_strategy_params()
        
        # 노출 한도 체크
        await self.check_exposure_limits(state_params)
        
        for symbol in TradeConfig.trade_config:
            if self.pos_info_dict[symbol].symbol_config.stop_trade:
                continue
            
            # 포지션 정보 업데이트
            self.pos_info_dict[symbol].update(BinanceTrader.client)
            
            # 현재 가격 가져오면서 이력 업데이트
            self.pos_info_dict[symbol].current_price()
            
            # 트레이드 실행
            await self.pos_info_dict[symbol].trade()
    
    async def get_risk_report(self) -> str:
        """리스크 관리 리포트"""
        report = "💰 **Risk Management Report**\n"
        report += "━━━━━━━━━━━━━━━━━━━━\n"
        
        total_exposure = 0
        total_max_exposure = 0
        
        for symbol in TradeConfig.trade_config:
            cfg = self.pos_info_dict[symbol].symbol_config
            pos_info = self.pos_info_dict[symbol]
            
            current_value = pos_info.position_amt['LONG'] * pos_info.price
            total_exposure += current_value
            total_max_exposure += cfg.max_amount
            
            report += f"\n**{symbol}**\n"
            report += f"• Current: ${current_value:,.0f}\n"
            report += f"• Max Allowed: ${cfg.max_amount:,.0f}\n"
            report += f"• Base Max: ${cfg.base_max_amount:,.0f}\n"
            report += f"• Utilization: {(current_value/cfg.max_amount*100 if cfg.max_amount > 0 else 0):.1f}%\n"
        
        report += f"\n**Total Portfolio**\n"
        report += f"• Current Exposure: ${total_exposure:,.0f}\n"
        report += f"• Max Allowed: ${total_max_exposure:,.0f}\n"
        report += f"• Portfolio Limit: ${TOTAL_PORTFOLIO_LIMIT:,.0f}\n"
        report += f"• Utilization: {(total_exposure/TOTAL_PORTFOLIO_LIMIT*100):.1f}%\n"
        
        return report
    
    async def check_exposure_limits(self, state_params):
        """노출 한도 체크"""
        total_exposure = 0
        alt_exposure = 0
        
        for symbol, pos_info in self.pos_info_dict.items():
            value = pos_info.position_amt['LONG'] * pos_info.entry_price['LONG']
            total_exposure += value
            
            if symbol not in ['BTC', 'ETH']:
                alt_exposure += value
        
        # 한도 초과 시 경고
        max_total = state_params.get('max_total_exposure', 1.0)
        max_alt = state_params.get('max_alt_exposure', 0.5)
        
        # BTC의 max_amount를 기준으로 계산
        base_amount = TradeConfig.trade_config.get('BTC', next(iter(TradeConfig.trade_config.values()))).max_amount
        
        if total_exposure > base_amount * max_total:
            logger.warning(f"Total exposure exceeds limit: ${total_exposure:.0f}")
        
        if alt_exposure > base_amount * max_alt:
            logger.warning(f"Alt exposure exceeds limit: ${alt_exposure:.0f}")
    
    async def reduce_position(self, symbol, ratio=0.5):
        """포지션 부분 청산"""
        if symbol in self.pos_info_dict:
            pos_info = self.pos_info_dict[symbol]
            pos = pos_info.position_amt['LONG']
            if pos > 0:
                sell_amount = pos * ratio
                price = pos_info.current_price()
                OrderManager.sell_market(
                    pos_info.symbol_config,
                    round(price, pos_info.symbol_config.price_precision),
                    round(sell_amount, pos_info.symbol_config.volume_precision)
                )
                logger.info(f"Reduced {symbol} position by {ratio:.0%}")
    
    async def close_position(self, symbol):
        """포지션 전체 청산"""
        await self.reduce_position(symbol, 1.0)
    
    async def get_position_analysis_report(self) -> str:
        """전체 자산 위치 분석 리포트"""
        report = "📊 **Position Analysis Report**\n"
        report += "━━━━━━━━━━━━━━━━━━━━\n"
        
        for symbol in TradeConfig.trade_config:
            score = position_analyzer.get_position_score(symbol)
            position_info = self.pos_info_dict[symbol]
            
            report += f"\n**{symbol}**\n"
            report += f"• Position: {score['position_label']}\n"
            report += f"• Score: {score['combined_score']:.1f}/100\n"
            report += f"• RSI: {score['rsi']:.1f}\n"
            report += f"• Momentum: {score['momentum']:+.1f}%\n"
            
            # 현재 포지션 정보
            pos = position_info.position_amt['LONG']
            if pos > 0:
                entry = position_info.entry_price['LONG']
                current = position_info.price
                profit = ((current - entry) / entry * 100) if entry > 0 else 0
                report += f"• Holdings: ${pos * current:.0f} ({profit:+.1f}%)\n"
            
        return report


# ==========================================
# Alarm Manager
# ==========================================

class AlarmManager:
    def __init__(self):
        global trader
        self.trader = trader
        self.last_state_check = datetime.now()
        self.running = True

    async def run(self):
        """메인 실행 루프"""
        while True:
            try:
                # 시장 상태 업데이트 및 트레이딩
                await self.trader.update_all()
                
                # 상태별 대기 시간
                state_params = state_manager.get_strategy_params()
                interval_hours = state_params['interval_hours']
                
                # 주기적 상태 보고 (4시간마다)
                if (datetime.now() - self.last_state_check).total_seconds() > 14400:
                    await self.report_status()
                    self.last_state_check = datetime.now()
                
                await asyncio.sleep(60 * 60 * interval_hours)
                
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(60)
    
    async def report_status(self):
        """주기적 상태 보고"""
        state_params = state_manager.get_strategy_params()
        
        msg = f"""
📊 **System Status Report**
━━━━━━━━━━━━━━━━━━━━
🔹 Market State: {state_manager.current_state}
🔹 Description: {state_params['description']}
🔹 Confidence: {state_manager.state_confidence:.1%}
🔹 Active Symbols: {', '.join(state_params['allowed_symbols'])}
━━━━━━━━━━━━━━━━━━━━
        """
        
        for symbol in TradeConfig.trade_config:
            if symbol in state_params['allowed_symbols']:
                pos_info = self.trader.pos_info_dict[symbol]
                pos_amt = pos_info.position_amt['LONG'] + pos_info.position_amt['SHORT']
                if pos_amt > 0:
                    msg += f"\n{symbol}: ${pos_amt * pos_info.current_price():,.0f}"
        
        await TelegramManager.send_message(msg)

# ==========================================
# Main Execution 수정
# ==========================================

async def main():
    """비동기 메인 함수"""
    global trader, t_alarm
    
    # 히스토리컬 데이터 초기화
    await trader.initialize_historical_data()

    # Initial state update
    await state_manager.update_state()
    
    # Start alarm manager
    t_alarm = AlarmManager()
    
    # Run alarm manager
    await t_alarm.run()

if __name__ == "__main__":
    logger.info('Starting Enhanced Trading Bot with Position Analysis')
    
    # Check if config files are provided
    if len(sys.argv) < 2:
        print("Usage: python3 adaptive_trading_bot.py <config1.yaml> [config2.yaml] ...")
        print("Please provide at least one YAML config file")
        sys.exit(1)
    
    # YAML 파일이 로드되었는지 확인
    if not TradeConfig.trade_config:
        logger.error("No trading configurations loaded")
        sys.exit(1)
    
    # trader 객체는 TradeConfig가 로드된 후에 생성
    trader = EnhancedTrader()
    
    # Test database connection
    if not db_manager.connection:
        logger.warning("Database not connected, will use default state S4")
    else:
        logger.info("Database connected successfully")
    
    # Register Telegram commands
    services = Services(
        trader=trader,
        state_manager=state_manager,
        db_manager=db_manager,
        trade_config=TradeConfig,
        logger=logger,
        binance_client=BinanceTrader.client,  # balance 명령에서 사용
    )
    cmds = Commands(services)
    register_bot_commands(application, cmds)
    
    # 두 개의 이벤트 루프를 동시에 실행
    loop = asyncio.get_event_loop()
    
    # 메인 트레이딩 루프를 백그라운드 태스크로 실행
    asyncio.ensure_future(main())
    
    # 텔레그램 봇 실행
    logger.info("Bot started successfully")
    print("Bot is running... Press Ctrl+C to stop")
    
    try:
        application.run_polling()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        if 't_alarm' in globals():
            t_alarm.running = False