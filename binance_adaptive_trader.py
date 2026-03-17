#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crypto Trading Bot - 3-Mode System (STOP / NORMAL / CAUTIOUS)
분류기(S0-S8) 결과를 3모드로 변환하여 단순하게 트레이딩
"""

import os
import sys
import yaml
import asyncio
import logging
import threading
from dotenv import load_dotenv
from datetime import datetime

# Telegram
import telegram
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

# Plotting
import matplotlib
matplotlib.use('Agg')

# My modules
from adaptive.position_analyzer import PositionAnalyzer
from adaptive.db_manager import DatabaseManager
from adaptive.commands import Services, Commands, register as register_bot_commands
from adaptive.market_state import MarketStateManager
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

TOTAL_PORTFOLIO_LIMIT = float(os.getenv('TOTAL_PORTFOLIO_LIMIT', '100000'))
print(f"Total Portfolio Limit: ${TOTAL_PORTFOLIO_LIMIT:,.0f}")

# Telegram Application
request = HTTPXRequest(
    connect_timeout=20.0,
    read_timeout=20.0,
    write_timeout=20.0,
    pool_timeout=10.0,
)
application = (
    ApplicationBuilder()
    .token(TELEGRAM_BOT_TOKEN)
    .request(request)
    .build()
)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Update {update} caused error {context.error}")
    if isinstance(context.error, telegram.error.NetworkError):
        logger.info("Network error, bot will auto-retry")

application.add_error_handler(error_handler)

# Logger
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
# Analyzers & Managers
# ==========================================
position_analyzer = PositionAnalyzer(max_history_length=600)
db_manager = DatabaseManager()

state_manager = MarketStateManager(
    db_manager=db_manager,
    send_message=TelegramManager.send_message,
    logger=logger,
)


# ==========================================
# Config Loader (단순화)
# ==========================================
class ConfigLoader:
    def __init__(self):
        pass

    def load(self, file_path):
        if not os.path.exists(file_path):
            print(f'File {file_path} does not exist.')
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
        print(f'-- [{self.symbol}] Amount: ${self.open_amount}, Max: ${self.max_amount}, '
              f'Strategy: {"VA" if self.is_va else "DCA"}, '
              f'TP: {self.take_profit_ratio}%, SL: {self.stop_loss_ratio}%')


class SymbolInfo:
    """심볼 설정 (단순화 - YAML 값 그대로 사용)"""

    def __init__(self, config_loader):
        self.symbol = config_loader.symbol
        self.symbol_binance = f'{config_loader.symbol}USDT'
        self.symbol_binance_order = f'{config_loader.symbol}/USDT'
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
        self.stop_trade = True  # 기본은 정지, /start_all 로 시작

    def __str__(self):
        return (
            f'{self.symbol}: ${self.open_amount:,} / Max ${self.max_amount:,} / '
            f'{"VA" if self.is_va else "DCA"} / '
            f'TP {self.take_profit_ratio}% / SL {self.stop_loss_ratio}%'
        )


# ==========================================
# Trade Config Manager
# ==========================================
class TradeConfig:
    trade_config = dict()
    current_symbol = ''
    thread_lock = threading.Lock()

    @classmethod
    def loads(cls):
        for i in range(1, len(sys.argv)):
            config_loader = ConfigLoader()
            config_loader.load(sys.argv[i])
            config_loader.print_config()
            cls.trade_config[config_loader.symbol] = SymbolInfo(config_loader)
            cls.current_symbol = config_loader.symbol

        # 포트폴리오 한도 체크
        total_max = sum(c.max_amount for c in cls.trade_config.values())
        if total_max > TOTAL_PORTFOLIO_LIMIT:
            scale = TOTAL_PORTFOLIO_LIMIT / total_max
            logger.warning(
                f"Total max (${total_max:,.0f}) exceeds limit "
                f"(${TOTAL_PORTFOLIO_LIMIT:,.0f}). Scaling by {scale:.2%}"
            )
            for cfg in cls.trade_config.values():
                cfg.max_amount *= scale
                cfg.open_amount *= scale

    @classmethod
    def current_symbol_config(cls):
        return cls.trade_config.get(cls.current_symbol)


# YAML 로드
if len(sys.argv) > 1:
    TradeConfig.loads()


# ==========================================
# Alarm Manager (메인 루프)
# ==========================================
class AlarmManager:
    def __init__(self, trader):
        self.trader = trader
        self.last_report_time = datetime.now()

    async def run(self):
        while True:
            try:
                # 트레이딩 실행
                await self.trader.update_all()

                # 모드별 대기 시간
                params = state_manager.mode_params
                interval_hours = params['interval_hours']

                # 4시간마다 상태 보고
                elapsed = (datetime.now() - self.last_report_time).total_seconds()
                if elapsed > 14400:
                    await self._report()
                    self.last_report_time = datetime.now()

                await asyncio.sleep(60 * 60 * interval_hours)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(60)

    async def _report(self):
        mode = state_manager.current_mode
        params = state_manager.mode_params

        lines = [
            f"📊 <b>Status Report</b>",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"State: <code>{state_manager.current_state}</code>",
            f"Mode: <b>{mode.value}</b> ({params['description']})",
        ]

        total_value = 0
        for symbol, pos in self.trader.pos_info_dict.items():
            amt = pos.position_amt['LONG']
            if amt > 0:
                value = amt * pos.price
                total_value += value
                lines.append(f"  {symbol}: ${value:,.0f}")

        if total_value > 0:
            lines.append(f"\nTotal: ${total_value:,.0f}")

        await TelegramManager.send_message("\n".join(lines))


# ==========================================
# Main
# ==========================================
async def main():
    global trader, alarm

    await trader.initialize_historical_data()
    await state_manager.update_state()

    alarm = AlarmManager(trader)
    await alarm.run()


if __name__ == "__main__":
    logger.info('Starting Trading Bot (3-Mode System)')

    if len(sys.argv) < 2:
        print("Usage: python3 adaptive_trading_bot.py <config1.yaml> [config2.yaml] ...")
        sys.exit(1)

    if not TradeConfig.trade_config:
        logger.error("No trading configurations loaded")
        sys.exit(1)

    # Trader 생성
    trader = EnhancedTrader(
        trade_config=TradeConfig.trade_config,
        position_analyzer=position_analyzer,
        state_manager=state_manager,
        send_message=TelegramManager.send_message,
        portfolio_limit=TOTAL_PORTFOLIO_LIMIT,
        logger=logger,
    )

    # 트레이더 콜백 연결
    state_manager.set_trader_callbacks(
        reduce_position_cb=trader.reduce_position,
        close_position_cb=trader.close_position,
    )

    # DB 연결 확인
    if db_manager.connection:
        logger.info("Database connected")
    else:
        logger.warning("Database not connected, using default state S4")

    # Telegram 명령어 등록
    services = Services(
        trader=trader,
        state_manager=state_manager,
        db_manager=db_manager,
        trade_config=TradeConfig,
        logger=logger,
        binance_client=BinanceTrader.client,
    )
    cmds = Commands(services)
    register_bot_commands(application, cmds)

    # 메인 루프 + 텔레그램 봇 동시 실행
    loop = asyncio.get_event_loop()
    asyncio.ensure_future(main())

    logger.info("Bot started (3-Mode: STOP / NORMAL / CAUTIOUS)")
    print("Bot is running... Press Ctrl+C to stop")

    try:
        application.run_polling()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")