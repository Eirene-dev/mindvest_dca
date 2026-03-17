# sudo apt update && sudo apt install python3-pip
# python3 -m pip install python-binance binance pyyaml==5.4.1 ccxt pyupbit python-telegram-bot
# python3 -m pip install mojito2
import websockets
import aiofiles
import asyncio
import json
import sys
import datetime
import threading
import time
import requests
import statistics
import math
import yaml
import sys
import os
import os.path
import logging
# import schedule
from datetime import datetime

from enum import Enum
from threading import Thread
import multiprocessing as mp

# from binance import AsyncClient, BinBinanceTraderanceSocketManager
# from binance.streams import ThreadedWebsocketManager, FuturesType
from binance.client import Client
import ccxt
import numpy as np
from scipy.spatial.distance import cdist

from telegram import ForceReply, Update
from telegram.ext import filters, CallbackContext, CommandHandler, ApplicationBuilder, ContextTypes, MessageHandler

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# TELEGRAM_BOT_TOKEN = '5748004151:AAHu8qmRNinZeCENhaOXvDp645ICGeo6nKk'
TELEGRAM_BOT_TOKEN = '5903401594:AAGR2yc4tiRqwm5s5k_wANm01KZYZohhqho'
CHAT_ID = 5435954678

# API_KEY = 'PddJSnVLpIR4reOBLidUYZV8JSiKuNs1enBFRDfmP14hDfNhyhxhACfQ54khbwUy'
# API_SEC = '0FDDBhVsRq8xC7kO0kHmK4aS09iJ48YLTMjLpiesVSxVT1QAEQYP0vSuzxzXhE0x'
API_KEY = '0inkTkuUrSamBq8TM6gyYhARbJTfJ6Bb9TxajzMxD0hXlcd2RQVimSxNRXW7YO0j'
API_SEC = '8v6mZLSQKn9SyBFLHmo81oSWgrcbVBpT1Kz4rpCGxklJV4NUhnDhqKbT8Q8B39AP'

# Setting a Telegram application
application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

class TelegramManager:
    @staticmethod
    async def send_message(msg):
        global application
        await application.bot.send_message(chat_id=CHAT_ID, text=msg)


class ConfigLoader():
    def __init__(self):
        pass

    def load_connection(self, file_path='connection.yaml'):
        if not os.path.exists(file_path):
            print('File {} is not exists.'.format(file_path))
            sys.exit(1)

        with open(file_path) as f:
            pass

    def load(self, file_path):
        if not os.path.exists(file_path):
            print('File {} is not exists.'.format(file_path))
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

            self.is_lao = conf['is_lao']

    def print_config(self):
        print('-- [ Config ] ----------------------------------------------------------------')
        print(f'Symbol {self.symbol}, Volume {self.open_amount}, LAO {self.is_lao}')
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

        self.leftover = 0

        self.stop_trade = False

    def __str__(self):
        return 'Symbol {}, Amount {:,},  Max {}, TakeProfit {}%, StopLoss {}%'.format(
            self.symbol, self.open_amount, self.max_amount,
            self.take_profit_ratio, self.stop_loss_ratio)


class TradeConfig:
    # load config files
    trade_config = dict()
    current_symbol = ''

    @classmethod
    def loads(cls):
        for i in range(1, len(sys.argv)):
            print('Loads {}'.format(sys.argv[i]))

            config_loader = ConfigLoader()
            config_loader.load(sys.argv[i])
            config_loader.print_config()

            cls.trade_config[config_loader.symbol] = SymbolInfo(config_loader)
            cls.current_symbol = config_loader.symbol

    @classmethod
    def load_new_symbol(cls, file_name):
        print('Loads {}'.format(file_name))
        try:
            config_loader = ConfigLoader()
            config_loader.load(file_name)
            config_loader.print_config()

            cls.trade_config[config_loader.symbol] = SymbolInfo(config_loader)
            cls.current_symbol = config_loader.symbol
        except Exception as e:
            logger.error(e)

    @classmethod
    def del_symbol(cls, symbol_name):
        try:
            del cls.trade_config[symbol_name]
        except KeyError as ex:
            print("No such key: '%s'" % ex.message)

        if symbol_name == cls.current_symbol:
            for s in cls.trade_config:
                cls.current_symbol = s
                break

    thread_lock = threading.Lock()
    thread_lock_volume = threading.Lock()
    thread_lock_pos = threading.Lock()

    range_min = 60 * 60 * 4            # 60 min * 4 = 4h

    # connection parameters
    binance_api_key = API_KEY
    binance_api_sec = API_SEC

    @classmethod
    def current_symbol_config(cls):
        return cls.trade_config[cls.current_symbol]

TradeConfig.loads()


# Set logging
logger = logging.getLogger(TradeConfig.current_symbol)

def set_logger():
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(name)s;%(asctime)s;%(levelname)s;%(lineno)d;%(message)s')

    stream_hander = logging.StreamHandler()
    stream_hander.setFormatter(formatter)
    logger.addHandler(stream_hander)

    file_handler = logging.FileHandler('{}.log'.format(TradeConfig.current_symbol))
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
set_logger()


def reconnect():
    return ccxt.binance(config={
        'apiKey': TradeConfig.binance_api_key,
        'secret': TradeConfig.binance_api_sec,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future'
        }
    })


class OrderManager():
    broker = reconnect()

    @classmethod
    def reconnect(cls):
        cls.broker = reconnect()

    @classmethod
    def buy_limit(cls, symbol_config, request_price, vol):
        if symbol_config.direction == 'LONG':
            BinanceTrader.open_long(symbol_config, request_price, vol, True)
        elif symbol_config.direction == 'SHORT':
            BinanceTrader.open_short(symbol_config, request_price, vol, True)

    @classmethod
    def sell_limit(cls, symbol_config, request_price, vol):
        if symbol_config.direction == 'LONG':
            BinanceTrader.close_long(symbol_config, request_price, vol, True)
        elif symbol_config.direction == 'SHORT':
            BinanceTrader.close_short(symbol_config, request_price, vol, True)

    @classmethod
    def buy_market(cls, symbol_config, request_price, vol):
        if symbol_config.direction == 'LONG':
            BinanceTrader.open_long(symbol_config, request_price, vol, False)
        elif symbol_config.direction == 'SHORT':
            BinanceTrader.open_short(symbol_config, request_price, vol, False)

    @classmethod
    def sell_market(cls, symbol_config, request_price, vol):
        if symbol_config.direction == 'LONG':
            BinanceTrader.close_long(symbol_config, request_price, vol, False)
        elif symbol_config.direction == 'SHORT':
            BinanceTrader.close_short(symbol_config, request_price, vol, False)


class PositionState(Enum):
    NoPos = 0
    HavePos = 1
    MaxPos = 2


class TrailingMode(Enum):
    Normal = 0
    Trailing = 1


class PosInfo:
    def __init__(self, symbol_config):
        self.symbol_config = symbol_config

        # 현재 시간에서 1시간(3600초)을 빼서 last_update_time을 설정
        self.last_update_time = time.time() - 3600

        # PosInfo
        self.position_amt = {'LONG':0, 'SHORT':0}
        self.entry_price = {'LONG':0.0, 'SHORT':100000.0}
        self.pos_state = {'LONG':PositionState.NoPos, 'SHORT':PositionState.NoPos}
        self.tp = {'LONG':symbol_config.take_profit_ratio, 'SHORT':symbol_config.take_profit_ratio}
        self.sl = {'LONG':symbol_config.stop_loss_ratio, 'SHORT':symbol_config.stop_loss_ratio}

        # Order Manager
        self.price = float(BinanceTrader.client.futures_symbol_ticker(symbol=symbol_config.symbol_binance)['price'])
        self.open_price = self.price
        self.close_price = self.price

        # Trend
        self.trend_bullish = False

    def position_ratio(self):
        max_amount = self.symbol_config.max_amount
        current_amount = self.position_amt['LONG'] * self.entry_price
        # current_volume = self.position_amt['LONG'] + self.position_amt['SHORT']
        return (current_amount / max_amount) * 100

    def _update_trend(self):
        client = OrderManager.broker

        def rational_quadratic_kernel(x, x_prime, alpha, h):
            return (1 + cdist(x, x_prime, 'sqeuclidean') / (2 * alpha * h ** 2)) ** (-alpha)

        def nadaraya_watson(x, y, x_prime, alpha, h, kernel_function):
            weights = kernel_function(x, x_prime, alpha, h)
            return np.sum(weights * y, axis=0) / np.sum(weights, axis=0)

        timeframe = '1h'
        limit = 500

        # Fetch the historical OHLCV data
        ohlcv = client.fetch_ohlcv(self.symbol_config.symbol_binance, timeframe, limit=limit)

        # Extract the closing prices
        closing_prices = [x[4] for x in ohlcv]

        # Reshape the data for regression
        x = np.arange(len(closing_prices)).reshape(-1, 1)
        y = np.array(closing_prices).reshape(-1, 1)

        # Define hyperparameters for the Nadaraya-Watson kernel regression
        alpha = 8
        h = 8

        # Perform the Nadaraya-Watson kernel regression
        y_pred = nadaraya_watson(x, y, x, alpha, h, rational_quadratic_kernel)

        return y_pred, closing_prices

    async def update_trend(self):
        y_pred, closing_prices = self._update_trend()

        was_bearish = y_pred[-3] > y_pred[-2]
        was_bullish = y_pred[-3] < y_pred[-2]
        is_bearish = y_pred[-2] > y_pred[-1]
        is_bullish = y_pred[-2] < y_pred[-1]

        is_bearish_change = is_bearish and was_bullish
        is_bullish_change = is_bullish and was_bearish

        # Telegram ----------------------------
        # if self.trend_bullish != is_bullish and (is_bearish_change or is_bullish_change):
        if self.trend_bullish != is_bullish:
            new_trend = 'Bullish' if is_bullish_change else 'Bearish'
            msg = '[{}] Trend: {}'.format(self.symbol_config.symbol, new_trend)
            # await TelegramManager.send_message(msg)
        # -------------------------------------

        self.trend_bullish = is_bullish

        return  y_pred, closing_prices


    def balance(self):
        direction = self.symbol_config.direction
        price = self.current_price()

        if direction == 'LONG':
            profit_ratio = ((price - self.entry_price['LONG']) / price) * 100.0 if self.entry_price['LONG'] > 0 else 0
        elif direction == 'SHORT':
            profit_ratio = ((self.entry_price['SHORT'] - price) / price) * 100.0 if self.entry_price['SHORT'] > 0 else 0
        else:
            profit_ratio = 0.0

        TradeConfig.thread_lock_pos.acquire()
        balance_str = 'Name: {} ({}) \nPosState: {} \nEntry: ${} \nPrice: ${} \nProfit Ratio: {:0,.2f}% \nSize: {} \nAmount: ${:0,.1f}({:0,.0f})'.format(
                    self.symbol_config.symbol, direction,
                    self.pos_state[direction],
                    self.entry_price[direction],
                    price,
                    profit_ratio,
                    self.position_amt[direction],
                    self.position_amt[direction]*price,
                    self.position_amt[direction]*price*1450)
        TradeConfig.thread_lock_pos.release()

        return balance_str

    def print_info(self, price):
        profit_ratio_long = ((price - self.entry_price['LONG']) / price) * 100.0 if self.entry_price['LONG'] > 0 else 0
        profit_ratio_short = ((self.entry_price['SHORT'] - price) / price) * 100.0 if self.entry_price['SHORT'] > 0 else 0

        logger.info('[LONG]  Price %f, Entry Price %f, Position %d, Profit Ratio %f' % (price, 
            self.entry_price['LONG'], self.position_amt['LONG'], round(profit_ratio_long, 2)))
        logger.info('[SHORT] Price %f, Entry Price %f, Position %d, Profit Ratio %f' % (price, 
            self.entry_price['SHORT'], self.position_amt['SHORT'], round(profit_ratio_short, 2)))

    def update(self, client):
        # get position information
        try:
            info = client.futures_position_information(symbol=self.symbol_config.symbol_binance)

            for item in info:
                if item['positionSide'] == 'BOTH':
                    continue
                TradeConfig.thread_lock_pos.acquire()
                self.position_amt[item['positionSide']] = abs(float(item['positionAmt']))
                self.entry_price[item['positionSide']] = float(item['entryPrice'])
                TradeConfig.thread_lock_pos.release()
        except Exception as e:
            logger.error(e)
            BinanceTrader.reconnect()
            return

        # change position state
        positions = ['LONG', 'SHORT']
        for pos in positions:
            TradeConfig.thread_lock_pos.acquire()
            if self.position_amt[pos] == 0:
                self.pos_state[pos] = PositionState.NoPos
            elif self.position_amt[pos] >= (self.symbol_config.max_amount / self.entry_price[pos]):
                self.pos_state[pos] = PositionState.MaxPos
            else:
                self.pos_state[pos] = PositionState.HavePos
            TradeConfig.thread_lock_pos.release()

    def check_stop_loss(self, price):
        if self.symbol_config.direction == 'LONG':
            return self.check_stop_loss_long(price)
        elif self.symbol_config.direction == 'SHORT':
            return self.check_stop_loss_short(price)

    def check_stop_loss_long(self, price):
        if price > self.symbol_config.lower_stop:
            return False

        # vol = self.position_amt['LONG'] - self.position_amt['SHORT']
        # BinanceTrader.open_short(self.symbol_config, price, vol, False)

        return True

    def check_stop_loss_short(self, price):
        if price < self.symbol_config.upper_stop:
            return False

        # vol = self.position_amt['SHORT'] - self.position_amt['LONG']
        # BinanceTrader.open_long(self.symbol_config, price, vol, False)

        return True

    def current_price(self):
        try:
            info = OrderManager.broker.fetch_ticker(self.symbol_config.symbol_binance)
            price = float(info['last'])
            self.price = price
        except Exception:
            price = self.price
            OrderManager.reconnect()

        return price

    async def trade(self):
        if self.symbol_config.direction == 'LONG':
            await self.trade_long()
        elif self.symbol_config.direction == 'SHORT':
            await self.trade_short()

    async def trade_long(self):
        price = self.current_price()

        TradeConfig.thread_lock_pos.acquire()
        entry_price = self.entry_price['LONG']
        pos = self.position_amt['LONG']
        TradeConfig.thread_lock_pos.release()

        profit_ratio = ((price - entry_price) / price) * 100.0 if entry_price > 0 else 0
        tp_ratio = self.symbol_config.take_profit_ratio
        sl_ratio = self.symbol_config.stop_loss_ratio
        total_buy_amount = pos * entry_price + self.symbol_config.open_amount
        max_amount = self.symbol_config.max_amount
        

        print('{} {} Profit: {:0,.2f}%, Target: {:0,.2f}%'.format(str(datetime.now()), self.symbol_config.symbol, profit_ratio, tp_ratio))

        if pos > 0 and profit_ratio >= tp_ratio:
            pos_to_sell = pos
            if self.symbol_config.is_lao:
                pos_to_sell = max(1, pos / 2)
            OrderManager.sell_market(self.symbol_config, round(price, self.symbol_config.price_precision), pos_to_sell)

            # Telegram ----------------------------
            msg = '**[{}][TP] Close Long: Profit {:0,.2f}% ({:0,.2f}%), {}'.format(self.symbol_config.symbol, profit_ratio, tp_ratio, self.position_amt['LONG'])
            await TelegramManager.send_message(msg)
            logging.info(msg)
            # -------------------------------------

            self.leftover = 0
        elif pos > 0 and profit_ratio <= sl_ratio:
            pos_to_sell = pos
            OrderManager.sell_market(self.symbol_config, round(price, self.symbol_config.price_precision), pos_to_sell)

            # Telegram ----------------------------
            msg = '**[{}][SL] Close Long: Profit {:0,.2f}% ({:0,.2f}%), {}'.format(self.symbol_config.symbol, profit_ratio, sl_ratio, self.position_amt['LONG'])
            await TelegramManager.send_message(msg)
            logging.info(msg)
            # -------------------------------------

            self.leftover = 0
        elif total_buy_amount < max_amount:
            
            buy_amount = self.symbol_config.open_amount
            if self.symbol_config.is_lao and profit_ratio >= 0:
                buy_amount /= 2
            self.symbol_config.leftover += buy_amount

            buy_volume = round(self.symbol_config.leftover / price)

            if buy_volume > 0:
                OrderManager.buy_market(self.symbol_config, round(price, self.symbol_config.price_precision), buy_volume)

                # Telegram ----------------------------
                msg = '[{}] Open Price {}, Qty ${:0,.0f}+{:0,.0f}, Profit {:0,.2f}%'.format(self.symbol_config.symbol,
                                                                                price,
                                                                                buy_volume*price,
                                                                                self.position_amt['LONG']*price,
                                                                                profit_ratio)
                await TelegramManager.send_message(msg)
                # -------------------------------------

            self.symbol_config.leftover -= buy_volume * price
        else:
            self.leftover = 0

            # Telegram ----------------------------
            msg = '**[{}][Max] Current ${:0,.2f} (Max ${:0,.2f}),'.format(self.symbol_config.symbol, pos * entry_price, max_amount)
            await TelegramManager.send_message(msg)
            logging.info(msg)
            # -------------------------------------
        
    async def trade_short(self):
        price = self.current_price()

        TradeConfig.thread_lock_pos.acquire()
        entry_price = self.entry_price['SHORT']
        pos = self.position_amt['SHORT']
        TradeConfig.thread_lock_pos.release()

        profit_ratio = ((price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0
        profit_ratio = -1.0 * profit_ratio

        tp_ratio = self.symbol_config.take_profit_ratio
        sl_ratio = self.symbol_config.stop_loss_ratio
        total_buy_amount = pos * entry_price + self.symbol_config.open_amount
        max_amount = self.symbol_config.max_amount
        

        print('{} {} Profit: {:0,.2f}%, Target: {:0,.2f}%'.format(str(datetime.now()), self.symbol_config.symbol, profit_ratio, tp_ratio))

        if pos > 0 and profit_ratio >= tp_ratio:
            pos_to_sell = pos
            
            if self.symbol_config.is_lao and pos > self.symbol_config.open_amount*4:
                pos_to_sell = max(1, round(pos / 2, self.symbol_config.volume_precision))
            OrderManager.sell_market(self.symbol_config, round(price, self.symbol_config.price_precision), pos_to_sell)

            # Telegram ----------------------------
            msg = '**[{}][TP] Close Short: Profit {:0,.2f}% ({:0,.2f}%), {}'.format(self.symbol_config.symbol, profit_ratio, tp_ratio, self.position_amt['SHORT'])
            await TelegramManager.send_message(msg)
            logging.info(msg)
            # -------------------------------------

            self.leftover = 0
        elif pos > 0 and profit_ratio <= sl_ratio:
            pos_to_sell = pos
            OrderManager.sell_market(self.symbol_config, round(price, self.symbol_config.price_precision), pos_to_sell)

            # Telegram ----------------------------
            msg = '**[{}][SL] Close Short: Profit {:0,.2f}% ({:0,.2f}%), {}'.format(self.symbol_config.symbol, profit_ratio, sl_ratio, self.position_amt['SHORT'])
            await TelegramManager.send_message(msg)
            logging.info(msg)
            # -------------------------------------

            self.leftover = 0
        elif total_buy_amount < max_amount:
            
            buy_amount = self.symbol_config.open_amount
            if self.symbol_config.is_lao and profit_ratio >= 0:
                buy_amount /= 2
            self.symbol_config.leftover += buy_amount

            buy_volume = round(self.symbol_config.leftover / price, self.symbol_config.volume_precision)

            if buy_volume > 0:
                OrderManager.buy_market(self.symbol_config, round(price, self.symbol_config.price_precision), buy_volume)

                # Telegram ----------------------------
                msg = '[{}] Open Price {}, Qty ${:0,.0f}+{:0,.0f}, Profit {:0,.2f}%'.format(self.symbol_config.symbol,
                                                                                price,
                                                                                buy_volume*price,
                                                                                self.position_amt['SHORT']*price,
                                                                                profit_ratio)
                await TelegramManager.send_message(msg)
                # -------------------------------------

            self.symbol_config.leftover -= buy_volume * price
        else:
            self.leftover = 0

            # Telegram ----------------------------
            msg = '**[{}][Max] Current ${:0,.2f} (Max ${:0,.2f}),'.format(self.symbol_config.symbol, pos * entry_price, max_amount)
            await TelegramManager.send_message(msg)
            logging.info(msg)
            # -------------------------------------

class BinanceTrader:
    client = Client(api_key=TradeConfig.binance_api_key, api_secret=TradeConfig.binance_api_sec)

    @classmethod
    def reconnect(cls):
        cls.client = Client(api_key=TradeConfig.binance_api_key, api_secret=TradeConfig.binance_api_sec)

    @classmethod
    def open_long(cls, symbol_config, price, quantity, is_limit):
        cls.create_order(symbol_config, price, 'BUY', 'LONG', quantity, is_limit)

    @classmethod
    def close_long(cls, symbol_config, price, quantity, is_limit):
        cls.create_order(symbol_config, price, 'SELL', 'LONG', quantity, is_limit)

    @classmethod
    def open_short(cls, symbol_config, price, quantity, is_limit):
        cls.create_order(symbol_config, price, 'SELL', 'SHORT', quantity, is_limit)

    @classmethod
    def close_short(cls, symbol_config, price, quantity, is_limit):
        cls.create_order(symbol_config, price, 'BUY', 'SHORT', quantity, is_limit)

    @classmethod
    def create_order(cls, symbol_config, price, side, position_side, quantity, is_limit):
        price = round(price, symbol_config.price_precision)
        try:
            if is_limit:
                order = cls.client.futures_create_order(
                    symbol=symbol_config.symbol_binance,
                    type='LIMIT',
                    timeInForce='GTC',
                    price=price,
                    side=side,
                    positionSide=position_side,
                    quantity=quantity
                )
            else:
                order = cls.client.futures_create_order(
                    symbol=symbol_config.symbol_binance,
                    type='MARKET',
                    side=side,
                    positionSide=position_side,
                    quantity=quantity
                )
            logger.info(order)
            # TelegramManager.send_message(order)
        except Exception as e:
            logger.error('{} {} {} {} {}'.format(symbol_config.symbol, price, position_side, quantity, str(e)))
            # logger.error(e)

    @classmethod
    def cancel_all_order(cls, symbol_binance):
        cls.client.futures_cancel_all_open_orders(symbol=symbol_binance)


class Trader:
    def __init__(self):
        self.pos_info_dict = dict()

        for symbol in TradeConfig.trade_config:
            self.pos_info_dict[symbol] = PosInfo(TradeConfig.trade_config[symbol])

    def add_symbol(self, symbol_name):
        self.pos_info_dict[symbol_name] = PosInfo(TradeConfig.trade_config[symbol_name])

    def del_symbol(self, symbol_name):
        try:
            del self.pos_info_dict[symbol_name]
        except KeyError as ex:
            print("No such key: '%s'" % ex.message)

    async def update_all(self):
        for symbol in TradeConfig.trade_config:
            if self.pos_info_dict[symbol].symbol_config.stop_trade:
                continue
            
            # 1. Update current postion info
            self.pos_info_dict[symbol].update(BinanceTrader.client)

            # 2. Trading
            await self.pos_info_dict[symbol].trade()
            

trader = Trader()


class AlarmManager(threading.Thread):

    def __init__(self):
        threading.Thread.__init__(self)

        global trader
        self.trader = trader

    async def run(self):
        while True:
            await self.trader.update_all()
            await asyncio.sleep(TradeConfig.range_min)


async def symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    output = TradeConfig.current_symbol
    await update.message.reply_text(output)


async def volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol_config = TradeConfig.current_symbol_config()

    if len(context.args) == 0:
        output = 'Trading Volume: {}'.format(symbol_config.volume)
    elif len(context.args) != 1:
        output = 'Set Volume Failed: {}'.format(str(context.args))
    else:
        try:
            vol = float(context.args[0])
            TradeConfig.thread_lock.acquire()
            symbol_config.open_amount = vol
            TradeConfig.thread_lock.release()

            output = 'Set Volume: {}'.format(str(vol))
        except Exception as e:
            output = 'Volume Failed: {}'.format(str(context.args[0]))
            print(e)

    await update.message.reply_text(output)


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trader
    trader.pos_info_dict[TradeConfig.current_symbol].update(BinanceTrader.client)

    output = trader.pos_info_dict[TradeConfig.current_symbol].balance()
    await update.message.reply_text(output)


async def direction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol_config = TradeConfig.current_symbol_config()

    if len(context.args) == 0:
        output = 'Direction: {}'.format(symbol_config.direction)
    elif len(context.args) != 1:
        output = 'Set Direction Failed: {}'.format(str(context.args))
    else:
        try:
            d = context.args[0]

            TradeConfig.thread_lock_volume.acquire()
            symbol_config.direction = d
            TradeConfig.thread_lock_volume.release()

            output = 'Set Direction: {}'.format(symbol_config.direction)
        except Exception as e:
            output = 'Set Direction Failed: {} {}'.format(str(context.args), str(e))
            print(e)

    await update.message.reply_text(output)


async def stop_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol_config = TradeConfig.current_symbol_config()

    if len(context.args) == 0:
        output = 'Stop Loss Mode: {}'.format(symbol_config.stop_trade)
    elif len(context.args) != 1:
        output = 'Set Stop Loss Mode Failed: {}'.format(str(context.args))
    else:
        try:
            mode = str(context.args[0])
            sell_mode = True if mode == 'on' else False

            TradeConfig.thread_lock.acquire()
            symbol_config.stop_trade = sell_mode
            TradeConfig.thread_lock.release()

            output = 'Set Stop Loss Mode: {}'.format(symbol_config.stop_trade)
        except Exception as e:
            output = 'Grid Stop Loss Failed: {}'.format(str(context.args))
            print(e)

    await update.message.reply_text(output)


async def take_profit_ratio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol_config = TradeConfig.current_symbol_config()

    if len(context.args) == 0:
        output = 'Target Profit Ratio: {}%'.format(symbol_config.take_profit_ratio)
    elif len(context.args) != 1:
        output = 'Set Target Profit Ratio Failed: {}'.format(str(context.args))
    else:
        try:
            ratio = float(context.args[0])

            TradeConfig.thread_lock.acquire()
            symbol_config.take_profit_ratio = ratio
            TradeConfig.thread_lock.release()

            output = 'Set Target Profit Ratio: {}%'.format(symbol_config.take_profit_ratio)
        except Exception as e:
            output = 'Target Profit Ratio Failed: {}'.format(str(context.args))
            print(e)

    await update.message.reply_text(output)


async def trend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trader
    symbol_config = TradeConfig.current_symbol_config()
    y_pred, closing_prices = await trader.pos_info_dict[TradeConfig.current_symbol].update_trend()

    is_bullish = trader.pos_info_dict[TradeConfig.current_symbol].trend_bullish
    output = "▲ Bullish" if is_bullish else "▽ Bearish"
    await update.message.reply_text(output)

    plt.plot(closing_prices, label=TradeConfig.current_symbol, color='grey', linewidth=1)
    roc = np.diff(y_pred)

    for i in range(len(y_pred) - 1):
        color = '#009988' if roc[i] >= 0 else '#CC3311'

        if roc[i] > 0 and roc[i-1] < 0:
            marker = '▲'
            plt.text(i + 0.5, y_pred[i+1], marker, color=color, fontsize=10, ha='center', va='center')
        elif roc[i] < 0 and roc[i-1] > 0:
            marker = '▼'
            plt.text(i + 0.5, y_pred[i+1], marker, color=color, fontsize=10, ha='center', va='center')

        plt.plot([i, i+1], [y_pred[i], y_pred[i+1]], color=color)

    # Add labels and legend
    plt.xlabel('Time: {}'.format(datetime.now()))
    plt.ylabel('Price: {}'.format(closing_prices[-1]))
    plt.legend()

    # Save the plot to an image file
    plt.savefig('prices_plot.png', dpi=300, bbox_inches='tight')
    plt.clf()

    async with aiofiles.open('prices_plot.png', 'rb') as f:
        data = await f.read()  # 파일 내용을 읽음
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=data, caption="트렌드")


async def update_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trader

    await trader.update_all()


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trader
    symbol_config = TradeConfig.current_symbol_config()

    output = '{} Price {}'.format(
                symbol_config.symbol,
                trader.pos_info_dict[TradeConfig.current_symbol].current_price())
    await update.message.reply_text(output)


async def list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trader
    for k in TradeConfig.trade_config:
        output = str(TradeConfig.trade_config[k])
        await update.message.reply_text(output)


async def new_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trader
    if len(context.args) != 1:
        output = 'Wrong input file name'
    else:
        file_name = str(context.args[0])
        TradeConfig.load_new_symbol(file_name)
        trader.add_symbol(TradeConfig.current_symbol)

        output = 'Loaded {}\n'.format(str(TradeConfig.trade_config[TradeConfig.current_symbol]))
        output = output + str(TradeConfig.trade_config[TradeConfig.current_symbol])
        await update.message.reply_text(output)


async def del_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trader
    if len(context.args) != 1:
        output = 'Wrong symbol name'
    else:
        symbol_name = str(context.args[0])
        TradeConfig.del_symbol(symbol_name)
        trader.del_symbol(symbol_name)

        output = 'Delete {}\n'.format(symbol_name)
        await update.message.reply_text(output)


async def current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        output = '{}'.format(str(TradeConfig.trade_config[TradeConfig.current_symbol]))
    elif len(context.args) == 1:
        try:
            symbol = str(context.args[0])

            if symbol not in TradeConfig.trade_config:
                output = 'Set Current Failed: {} {}'.format(str(context.args))
            else:
                TradeConfig.thread_lock.acquire()
                TradeConfig.current_symbol = symbol
                TradeConfig.thread_lock.release()

                output = 'Set Current {}'.format(str(TradeConfig.trade_config[TradeConfig.current_symbol]))
        except Exception as e:
            output = 'Set Current Failed: {}\n{}'.format(str(context.args), str(e))
            print(e)

    await update.message.reply_text(output)


if __name__ == "__main__":
    logger.info('Start DCA Trading')

    application.add_handler(CommandHandler("symbol", symbol))
    application.add_handler(CommandHandler("volume", volume))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("direction", direction))
    application.add_handler(CommandHandler("stop_trade", stop_trade))
    application.add_handler(CommandHandler("take_profit_ratio", take_profit_ratio))
    application.add_handler(CommandHandler("trend", trend))
    application.add_handler(CommandHandler("update_all", update_all))
    application.add_handler(CommandHandler("price", price))
    application.add_handler(CommandHandler("list", list))
    application.add_handler(CommandHandler("new_symbol", new_symbol))
    application.add_handler(CommandHandler("del_symbol", del_symbol))
    application.add_handler(CommandHandler("current", current))

    # Timer
    t_alarm = AlarmManager()

    # Asynchronous jobs
    asyncio.ensure_future(t_alarm.run())

    # Start a Telegram application
    application.run_polling()
