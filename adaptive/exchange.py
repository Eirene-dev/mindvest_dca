# adaptive/exchange.py
import ccxt
from binance.client import Client
import logging

class BinanceTrader:
    client = None
    _api_key = None
    _api_sec = None

    @classmethod
    def init(cls, api_key: str, api_secret: str):
        cls._api_key = api_key
        cls._api_sec = api_secret
        cls.reconnect()

    @classmethod
    def reconnect(cls):
        cls.client = Client(api_key=cls._api_key, api_secret=cls._api_sec)

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
            logging.getLogger('AdaptiveTrader').info(order)
        except Exception as e:
            logging.getLogger('AdaptiveTrader').error(
                f'{symbol_config.symbol} {price} {position_side} {quantity} {str(e)}'
            )

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


class OrderManager:
    broker = None
    _api_key = None
    _api_sec = None

    @classmethod
    def init(cls, api_key: str, api_secret: str):
        cls._api_key = api_key
        cls._api_sec = api_secret
        cls.reconnect()

    @classmethod
    def reconnect(cls):
        cls.broker = ccxt.binance(config={
            'apiKey': cls._api_key,
            'secret': cls._api_sec,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })

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
