import re
from collections import defaultdict

import ccxt
import pandas as pd
from catalyst.assets._assets import TradingPair
from logbook import Logger

from catalyst.constants import LOG_LEVEL
from catalyst.exchange.exchange import Exchange
from catalyst.exchange.exchange_bundle import ExchangeBundle
from catalyst.exchange.exchange_errors import InvalidHistoryFrequencyError, \
    ExchangeSymbolsNotFound
from catalyst.exchange.exchange_utils import mixin_market_params, \
    from_ms_timestamp

log = Logger('CCXT', level=LOG_LEVEL)


class CCXT(Exchange):
    def __init__(self, exchange_name, key, secret, base_currency,
                 portfolio=None):
        log.debug(
            'finding {} in CCXT exchanges:\n{}'.format(
                exchange_name, ccxt.exchanges
            )
        )
        try:
            exchange_attr = getattr(ccxt, exchange_name)
            self.api = exchange_attr({
                'apiKey': key,
                'secret': secret,
            })
        except Exception:
            raise ValueError('exchange not in CCXT')

        markets = self.api.load_markets()
        log.debug('the markets:\n{}'.format(markets))

        self.name = exchange_name

        self.assets = dict()
        self.load_assets()

        self.base_currency = base_currency
        self._portfolio = portfolio
        self.transactions = defaultdict(list)

        self.num_candles_limit = 2000
        self.max_requests_per_minute = 60
        self.request_cpt = dict()

        self.bundle = ExchangeBundle(self.name)

    def account(self):
        return None

    def time_skew(self):
        return None

    def get_symbol(self, asset):
        parts = asset.symbol.split('_')
        return '{}/{}'.format(parts[0].upper(), parts[1].upper())

    def get_catalyst_symbol(self, market):
        parts = market['symbol'].split('/')
        return '{}_{}'.format(parts[0].lower(), parts[1].lower())

    def get_timeframe(self, freq):
        freq_match = re.match(r'([0-9].*)?(m|M|d|D|h|H|T)', freq, re.M | re.I)
        if freq_match:
            candle_size = int(freq_match.group(1)) \
                if freq_match.group(1) else 1

            unit = freq_match.group(2)

        else:
            raise InvalidHistoryFrequencyError(frequency=freq)

        if unit.lower() == 'd':
            timeframe = '{}d'.format(candle_size)

        elif unit.lower() == 'm' or unit == 'T':
            timeframe = '{}m'.format(candle_size)

        elif unit.lower() == 'h' or unit == 'T':
            timeframe = '{}h'.format(candle_size)

        return timeframe

    def get_candles(self, freq, assets, bar_count=None, start_dt=None,
                    end_dt=None):
        symbols = self.get_symbols(assets)
        timeframe = self.get_timeframe(freq)
        delta = start_dt - pd.to_datetime('1970-1-1', utc=True)
        ms = int(delta.total_seconds()) * 1000

        ohlcvs = self.api.fetch_ohlcv(
            symbol=symbols[0],
            timeframe=timeframe,
            since=ms,
            limit=bar_count,
            params={}
        )

        candles = []
        for ohlcv in ohlcvs:
            candles.append(dict(
                last_traded=pd.to_datetime(ohlcv[0], unit='ms', utc=True),
                open=ohlcv[1],
                high=ohlcv[2],
                low=ohlcv[3],
                close=ohlcv[4],
                volume=ohlcv[5]
            ))
        return candles

    def _fetch_symbol_map(self, is_local):
        try:
            return self.fetch_symbol_map(is_local)
        except ExchangeSymbolsNotFound:
            return None

    def _fetch_asset(self, market_id, is_local=False):
        symbol_map = self._fetch_symbol_map(is_local)
        if symbol_map is not None:
            assets_lower = {k.lower(): v for k, v in symbol_map.items()}
            key = market_id.lower()

            asset = assets_lower[key] if key in assets_lower else None
            if asset is not None:
                return asset, is_local

            elif not is_local:
                return self._fetch_asset(market_id, True)

            else:
                return None, is_local

        elif not is_local:
            return self._fetch_asset(market_id, True)

        else:
            return None, is_local

    def load_assets(self):
        markets = self.api.fetch_markets()

        for market in markets:
            asset, is_local = self._fetch_asset(market['id'])
            data_source = 'local' if is_local else 'catalyst'

            params = dict(
                exchange=self.name,
                data_source=data_source,
                exchange_symbol=market['id'],
            )
            mixin_market_params(self.name, params, market)

            if asset is not None:
                params['symbol'] = asset['symbol']

                params['start_date'] = pd.to_datetime(
                    asset['start_date'], utc=True
                ) if 'start_date' in asset else None

                params['end_date'] = pd.to_datetime(
                    asset['end_date'], utc=True
                ) if 'end_date' in asset else None

                params['leverage'] = asset['leverage'] \
                    if 'leverage' in asset else 1.0

                params['asset_name'] = asset['asset_name'] \
                    if 'asset_name' in asset else None

                params['end_daily'] = pd.to_datetime(
                    asset['end_daily'], utc=True
                ) if 'end_daily' in asset and asset['end_daily'] != 'N/A' \
                    else None

                params['end_minute'] = pd.to_datetime(
                    asset['end_minute'], utc=True
                ) if 'end_minute' in asset and asset['end_minute'] != 'N/A' \
                    else None

            else:
                params['symbol'] = self.get_catalyst_symbol(market)

            trading_pair = TradingPair(**params)
            self.assets[market['id']] = trading_pair

    def get_balances(self):
        return None

    def create_order(self, asset, amount, is_buy, style):
        return None

    def get_open_orders(self, asset):
        return None

    def get_order(self, order_id):
        return None

    def cancel_order(self, order_param):
        return None

    def tickers(self, assets):
        """
        Retrieve current tick data for the given assets

        Parameters
        ----------
        assets: list[TradingPair]

        Returns
        -------
        list[dict[str, float]

        """
        tickers = dict()
        for asset in assets:
            ccxt_symbol = self.get_symbol(asset)
            ticker = self.api.fetch_ticker(ccxt_symbol)

            ticker['last_traded'] = from_ms_timestamp(ticker['timestamp'])

            # Using the volume represented in the base currency
            ticker['volume'] = ticker['baseVolume'] \
                if 'baseVolume' in ticker else 0

            tickers[asset] = ticker

        return tickers

    def get_account(self):
        return None

    def get_orderbook(self, asset, order_type='all', limit=None):
        ccxt_symbol = self.get_symbol(asset)

        params = dict()
        if limit is not None:
            params['depth'] = limit

        order_book = self.api.fetch_order_book(ccxt_symbol, params)

        order_types = ['bids', 'asks'] if order_type == 'all' else [order_type]
        result = dict(last_traded=from_ms_timestamp(order_book['timestamp']))
        for index, order_type in enumerate(order_types):
            if limit is not None and index > limit - 1:
                break

            result[order_type] = []
            for entry in order_book[order_type]:
                result[order_type].append(dict(
                    rate=float(entry[0]),
                    quantity=float(entry[1])
                ))

        return result
