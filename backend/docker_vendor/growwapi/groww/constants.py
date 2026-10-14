"""
Constants for the Groww API and Feed
"""

from typing import Final
from enum import Enum

from growwapi.groww.client import GrowwAPI


class FeedConstants:
  """
  A class to generate subscription topics for various market data feeds.
  """

  DERIVATIVES_ORDER_UPDATES: Final[str] = "stocks_fo/order/updates.apex."
  DERIVATIVES_POSITION_UPDATES: Final[str] = "stocks_fo/position/updates.apex."
  EQUITY_ORDER_UPDATES: Final[str] = "stocks/order/updates.apex."

  DERIVATIVES_NSE_LIVE_PRICES: Final[
    str] = "/ld/fo/nse/price."
  DERIVATIVES_BSE_LIVE_PRICES: Final[
    str] = "/ld/fo/bse/price."

  DERIVATIVES_NSE_LIVE_PRICES_DETAIL: Final[
    str] = "/ld/fo/nse/price_detailed."
  DERIVATIVES_BSE_LIVE_PRICES_DETAIL: Final[
    str] = "/ld/fo/bse/price_detailed."

  DERIVATIVES_NSE_MARKET_DEPTH: Final[
    str] = "/ld/fo/nse/book."
  DERIVATIVES_BSE_MARKET_DEPTH: Final[
    str] = "/ld/fo/bse/book."

  EQUITY_NSE_LIVE_PRICES: Final[
    str] = "/ld/eq/nse/price."
  EQUITY_BSE_LIVE_PRICES: Final[
    str] = "/ld/eq/bse/price."

  EQUITY_NSE_LIVE_PRICES_DETAILED: Final[
    str] = "/ld/eq/nse/price_detailed."
  EQUITY_BSE_LIVE_PRICES_DETAILED: Final[
    str] = "/ld/eq/bse/price_detailed."

  EQUITY_NSE_MARKET_DEPTH: Final[
    str] = "/ld/eq/nse/book."
  EQUITY_BSE_MARKET_DEPTH: Final[
    str] = "/ld/eq/bse/book."

  NSE_LIVE_INDEX: Final[str] = "/ld/indices/nse/price."
  BSE_LIVE_INDEX: Final[str] = "/ld/indices/bse/price."

  EXCHANGE = "exchange"
  SEGMENT = "segment"
  FEED_KEY = "feed_key"
  FEED_TYPE = "feed_type"
  EXCHANGE_TOKEN = "exchange_token"

  LIVE_DATA = "ltp"
  ORDER_UPDATE = "order_updates"
  POSITION_UPDATE = "position_updates"
  MARKET_DEPTH = "market_depth"
  LIVE_DATA_DETAILED = "live_data_detailed"
  LIVE_INDEX = "index_value"

  class Topic:
    """
    A class to represent the topic for different subscriptions.
    """

    def __init__(self, topic: str, meta: {}):
      self.topic = topic
      self.meta = meta

    def get_topic(self):
      return self.topic

    def get_meta(self):
      return self.meta
  
  @staticmethod
  def _generate_topic_meta(exchange: str, segment: str, feed_key: str, feed_type: str) -> {}:
    """
    Generate a topic meta object.
    """
    return {
      FeedConstants.EXCHANGE: exchange,
      FeedConstants.SEGMENT: segment,
      FeedConstants.FEED_KEY: feed_key,
      FeedConstants.FEED_TYPE: feed_type
    }

  @staticmethod
  def get_derivatives_order_updates_topic(subscription_key: str) -> Topic:
    """
    Get the derivatives order updates topic.

    Args:
        subscription_key (str): The subscription key.

    Returns:
        str: The derivatives order updates topic.
    """
    return FeedConstants.Topic(f"{FeedConstants.DERIVATIVES_ORDER_UPDATES}{subscription_key}",
      FeedConstants._generate_topic_meta(None,
                                         GrowwAPI.SEGMENT_FNO,
                                         None,
                                         FeedConstants.ORDER_UPDATE))


  @staticmethod
  def get_derivatives_position_updates_topic(subscription_key: str) -> Topic:
    """
    Get the derivatives position updates topic.

    Args:
        subscription_key (str): The subscription key.

    Returns:
        str: The derivatives position updates topic.
    """
    return FeedConstants.Topic(f"{FeedConstants.DERIVATIVES_POSITION_UPDATES}{subscription_key}",
      FeedConstants._generate_topic_meta(None,
                                         GrowwAPI.SEGMENT_FNO,
                                         None,
                                         FeedConstants.POSITION_UPDATE))

  @staticmethod
  def get_equity_order_updates_topic(subscription_key: str) -> Topic:
    """
    Get the equity order updates topic.

    Args:
        subscription_key (str): The subscription key.

    Returns:
        str: The order updates topic.
    """
    return FeedConstants.Topic(f"{FeedConstants.EQUITY_ORDER_UPDATES}{subscription_key}",
      FeedConstants._generate_topic_meta(None,
                                         GrowwAPI.SEGMENT_CASH,
                                         None,
                                         FeedConstants.ORDER_UPDATE))

  @staticmethod
  def get_live_price_topic(segment: str, exchange: str, subscription_key: str) -> Topic:
    """
    Get the live trading price topic.

    Args:
        segment (str): The segment type (equity or derivatives).
        exchange (str): The exchange type (NSE or BSE).
        subscription_key (str): The subscription key.

    Returns:
        str: The live trading price topic.
    """
    if segment == GrowwAPI.SEGMENT_FNO:
      return FeedConstants.get_derivatives_live_price_topic(exchange,
                                                            subscription_key)
    if segment == GrowwAPI.SEGMENT_CASH:
      return FeedConstants.get_equity_live_price_topic(exchange,
                                                       subscription_key)
    raise ValueError(
      f"Invalid segment: {segment}. Must be either {GrowwAPI.SEGMENT_FNO}, {GrowwAPI.SEGMENT_CASH}")

  @staticmethod
  def get_live_price_detailed_topic(segment: str, exchange: str,
      subscription_key: str):
    """
    Get the live trading price detailed topic.

    Args:
        segment (str): The segment type (equity or derivatives).
        exchange (str): The exchange type (NSE or BSE).
        subscription_key (str): The subscription key.

    Returns:
        str: The live trading price detailed topic.
    """
    if segment == GrowwAPI.SEGMENT_FNO:
      return FeedConstants.get_derivatives_live_price_detailed_topic(exchange,
                                                                     subscription_key)
    if segment == GrowwAPI.SEGMENT_CASH:
      return FeedConstants.get_equity_live_price_detailed_topic(exchange,
                                                                subscription_key)
    raise ValueError(
      f"Invalid segment: {segment}. Must be either {GrowwAPI.SEGMENT_FNO}, {GrowwAPI.SEGMENT_CASH}.")

  @staticmethod
  def get_market_depth(segment: str, exchange: str, subscription_key: str):
    """
    Get the live market depth topic.

    Args:
        segment (str): The segment type (equity or derivatives).
        exchange (str): The exchange type (NSE or BSE).
        subscription_key (str): The subscription key.

    Returns:
        str: The live market depth topic.
    """
    if segment == GrowwAPI.SEGMENT_FNO:
      return FeedConstants.get_derivatives_market_depth_topic(exchange,
                                                              subscription_key)
    if segment == GrowwAPI.SEGMENT_CASH:
      return FeedConstants.get_equity_market_depth_topic(exchange,
                                                         subscription_key)
    raise ValueError(
      f"Invalid segment: {segment}. Must be either {GrowwAPI.SEGMENT_FNO}, {GrowwAPI.SEGMENT_CASH}")

  @staticmethod
  def get_derivatives_live_price_topic(exchange: str, subscription_key: str) -> Topic:
    """
    Get the derivatives NSE live trading price topic.

    Args:
        exchange (str): The exchange type (NSE or BSE).
        subscription_key (str): The subscription key.

    Returns:
        str: The derivatives NSE live trading price topic.
    """
    if exchange == GrowwAPI.EXCHANGE_BSE:
      return FeedConstants.Topic(f"{FeedConstants.DERIVATIVES_BSE_LIVE_PRICES}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_BSE, 
                                         GrowwAPI.SEGMENT_FNO,
                                         subscription_key,
                                         FeedConstants.LIVE_DATA))
    if exchange == GrowwAPI.EXCHANGE_NSE:
      return FeedConstants.Topic(f"{FeedConstants.DERIVATIVES_NSE_LIVE_PRICES}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_NSE, 
                                         GrowwAPI.SEGMENT_FNO,
                                         subscription_key,
                                         FeedConstants.LIVE_DATA))
    raise ValueError(
      f"Invalid exchange: {exchange}. Must be either NSE or BSE.")

  @staticmethod
  def get_derivatives_live_price_detailed_topic(exchange: str, subscription_key: str) -> Topic:
    """
    Get the derivatives NSE live trading price detailed topic.

    Args:
        exchange (str): The exchange type (NSE or BSE).
        subscription_key (str): The subscription key.

    Returns:
        str: The derivatives NSE live trading price detailed topic.
    """
    if exchange == GrowwAPI.EXCHANGE_BSE:
      return FeedConstants.Topic(f"{FeedConstants.DERIVATIVES_BSE_LIVE_PRICES_DETAIL}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_BSE, 
                                         GrowwAPI.SEGMENT_FNO,
                                         subscription_key,
                                         FeedConstants.LIVE_DATA_DETAILED))
    if exchange == GrowwAPI.EXCHANGE_NSE:
      return FeedConstants.Topic(f"{FeedConstants.DERIVATIVES_NSE_LIVE_PRICES_DETAIL}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_NSE, 
                                         GrowwAPI.SEGMENT_FNO,
                                         subscription_key,
                                         FeedConstants.LIVE_DATA_DETAILED))
    raise ValueError(
      f"Invalid exchange: {exchange}. Must be either NSE or BSE.")

  @staticmethod
  def get_derivatives_market_depth_topic(exchange: str, subscription_key: str) -> Topic:
    """
    Get the derivatives NSE live market depth topic.

    Args:
        exchange (str): The exchange type (NSE or BSE).
        subscription_key (str): The subscription key.

    Returns:
        str: The derivatives NSE live market depth topic.
    """
    if exchange == GrowwAPI.EXCHANGE_BSE:
      return FeedConstants.Topic(f"{FeedConstants.DERIVATIVES_BSE_MARKET_DEPTH}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_BSE, 
                                         GrowwAPI.SEGMENT_FNO,
                                         subscription_key,
                                         FeedConstants.MARKET_DEPTH))
    if exchange == GrowwAPI.EXCHANGE_NSE:
      return FeedConstants.Topic(f"{FeedConstants.DERIVATIVES_NSE_MARKET_DEPTH}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_NSE, 
                                         GrowwAPI.SEGMENT_FNO,
                                         subscription_key,
                                         FeedConstants.MARKET_DEPTH))
    raise ValueError(
      f"Invalid exchange: {exchange}. Must be either NSE or BSE.")
    
    
  

  @staticmethod
  def get_equity_live_price_topic(exchange: str, subscription_key: str) -> Topic:
    """
    Get the equity NSE live trading price topic.

    Args:
        exchange (str): The exchange type (NSE or BSE).
        subscription_key (str): The subscription key.

    Returns:
        str: The equity NSE live trading price topic.
    """
    if exchange == GrowwAPI.EXCHANGE_BSE:
      return FeedConstants.Topic(f"{FeedConstants.EQUITY_BSE_LIVE_PRICES}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_BSE, 
                                         GrowwAPI.SEGMENT_CASH,
                                         subscription_key,
                                         FeedConstants.LIVE_DATA))
    if exchange == GrowwAPI.EXCHANGE_NSE:
      return FeedConstants.Topic(f"{FeedConstants.EQUITY_NSE_LIVE_PRICES}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_NSE, 
                                         GrowwAPI.SEGMENT_CASH,
                                         subscription_key,
                                         FeedConstants.LIVE_DATA))
    raise ValueError(
      f"Invalid exchange: {exchange}. Must be either NSE or BSE.")

  @staticmethod
  def get_equity_live_price_detailed_topic(exchange: str,
      subscription_key: str) -> Topic:
    """
    Get the equity NSE live trading price detailed topic.

    Args:
        exchange (str): The exchange type (NSE or BSE).
        subscription_key (str): The subscription key.

    Returns:
        str: The equity NSE live trading price detailed topic.
    """
    if exchange == GrowwAPI.EXCHANGE_BSE:
      return FeedConstants.Topic(f"{FeedConstants.EQUITY_BSE_LIVE_PRICES_DETAILED}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_BSE, 
                                         GrowwAPI.SEGMENT_CASH,
                                         subscription_key,
                                         FeedConstants.LIVE_DATA_DETAILED))
    if exchange == GrowwAPI.EXCHANGE_NSE:
      return FeedConstants.Topic(f"{FeedConstants.EQUITY_NSE_LIVE_PRICES_DETAILED}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_NSE, 
                                         GrowwAPI.SEGMENT_CASH,
                                         subscription_key,
                                         FeedConstants.LIVE_DATA_DETAILED))
    raise ValueError(
      f"Invalid exchange: {exchange}. Must be either NSE or BSE.")

  @staticmethod
  def get_equity_market_depth_topic(exchange: str,
      subscription_key: str) -> Topic:
    """
    Get the equity NSE live market depth topic.

    Args:
        exchange (str): The exchange type (NSE or BSE).
        subscription_key (str): The subscription key.

    Returns:
        str: The equity NSE live market depth topic.
    """
    if exchange == GrowwAPI.EXCHANGE_BSE:
      return FeedConstants.Topic(f"{FeedConstants.EQUITY_BSE_MARKET_DEPTH}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_BSE, 
                                         GrowwAPI.SEGMENT_CASH,
                                         subscription_key,
                                         FeedConstants.MARKET_DEPTH))
    if exchange == GrowwAPI.EXCHANGE_NSE:
      return FeedConstants.Topic(f"{FeedConstants.EQUITY_NSE_MARKET_DEPTH}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_NSE, 
                                         GrowwAPI.SEGMENT_CASH,
                                         subscription_key,
                                         FeedConstants.MARKET_DEPTH))
    raise ValueError(
      f"Invalid exchange: {exchange}. Must be either NSE or BSE.")

  @staticmethod
  def get_live_index_topic(segment: str, exchange: str, subscription_key: str) -> Topic:
    """
    Get the live index topic.

    Args:
        exchange (str): The exchange type (NSE or BSE).
        subscription_key (str): The subscription key.

    Returns:
        str: The live index topic.
    """
    if exchange == GrowwAPI.EXCHANGE_BSE:
      return FeedConstants.Topic(
        f"{FeedConstants.BSE_LIVE_INDEX}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_BSE, 
                                         GrowwAPI.SEGMENT_CASH,
                                         subscription_key,
                                         FeedConstants.LIVE_INDEX))
    if exchange == GrowwAPI.EXCHANGE_NSE:
      return FeedConstants.Topic(
        f"{FeedConstants.NSE_LIVE_INDEX}{subscription_key}",
        FeedConstants._generate_topic_meta(GrowwAPI.EXCHANGE_NSE, 
                                         GrowwAPI.SEGMENT_CASH,
                                         subscription_key,
                                         FeedConstants.LIVE_INDEX))
    raise ValueError(
      f"Invalid exchange: {exchange}. Must be either NSE or BSE.")
