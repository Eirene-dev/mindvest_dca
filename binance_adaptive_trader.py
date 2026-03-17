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

# Telegram
from telegram import ForceReply, Update
from telegram.ext import filters, CallbackContext, CommandHandler, ApplicationBuilder, ContextTypes, MessageHandler
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

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
from adaptive.trader import EnhancedTrader

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
# HTTPXRequest with timeout settings
request = HTTPXRequest(
    connect_timeout=20.0,
    read_timeout=20.0,
    write_timeout=20.0,
    pool_timeout=10.0,
)

# Telegram Application 수정
application = (
    ApplicationBuilder()
    .token(TELEGRAM_BOT_TOKEN)
    .request(request)
    .build()
)

# 간단한 에러 핸들러 추가
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """에러 로깅만 수행"""
    logger.error(f"Update {update} caused error {context.error}")
    
    if isinstance(context.error, telegram.error.NetworkError):
        logger.info("Network error occurred, bot will auto-retry")

# 에러 핸들러 등록
application.add_error_handler(error_handler)

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
    async def send_message(msg: str):
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

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
        self.stop_trade = True

    def __str__(self):
        return 'Symbol {}, Amount {:,}, Max {}, Strategy {}, TP {}%, SL {}%'.format(
            self.symbol, self.open_amount, self.max_amount,
            'VA' if self.is_va else 'DCA',
            self.take_profit_ratio, self.stop_loss_ratio)

class AdaptiveSymbolInfo(SymbolInfo):
    """시장 상태와 자산 위치를 모두 고려하는 심볼 정보"""
    
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
        elif ('DEFAULT', 'DEFAULT') in POSITION_ADJUSTMENT_MATRIX:
            adjustments = POSITION_ADJUSTMENT_MATRIX[('DEFAULT', 'DEFAULT')]
        else:
            # 최후의 fallback
            logger.warning(f"No adjustment found for {matrix_key}, using hardcoded defaults")
            adjustments = {
                'amount_mult': 1.0,
                'max_mult': 1.0,
                'tp_mult': 1.0,
                'sl_mult': 1.0,
            }
        
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
    trader = EnhancedTrader(
        trade_config=TradeConfig.trade_config,
        position_analyzer=position_analyzer,
        state_manager=state_manager,
        send_message=TelegramManager.send_message,
        portfolio_limit=TOTAL_PORTFOLIO_LIMIT,
        logger=logger,
    )
    
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