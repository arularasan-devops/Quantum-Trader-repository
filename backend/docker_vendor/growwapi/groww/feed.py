"""
The module provides classes to subscribe to the Groww feed and get the feed data.
"""
from __future__ import annotations

import os

import nacl.signing
import nkeys
import threading
from typing import Callable, Optional, Final, Any

from growwapi.groww.exceptions import GrowwFeedNotSubscribedException
from growwapi.groww.nats_client import NatsClient
from growwapi.groww.constants import FeedConstants
from growwapi.groww.client import GrowwAPI
from growwapi.groww.proto.proto_parser import get_data_dict


class Feed:
    """
    Feed class to store data for a given topic.
    """

    def __init__(
        self,
        topic: str,
        on_update: Optional[Callable[[], None]] = None,
        meta: Optional[{}] = None,
    ) -> None:
        """Initialize a Feed with a topic and no data.

        Args:
            topic (str): The topic of the feed.
            on_update (Optional[Callable[[], None]]): The callback function to call on data update.
        """
        self.topic: str = topic
        self.meta: Optional[{}] = meta
        self.on_update: Optional[Callable[[], None]] = on_update
        self.data: Optional[any] = None

    def update(self, data: any) -> None:
        """Update the feed with new data.

        Args:
            data (any): The new data for the feed.
        """
        self.data = data
        self.on_update(self.get_meta()) if self.on_update else None

    def get_data(self) -> Optional[any]:
        """Retrieve the data from the feed.

        Returns:
            Optional[any]: The data if available, else None.
        """
        return self.data

    def get_topic(self) -> str:
        """Retrieve the topic of the feed.

        Returns:
            str: The topic of the feed.
        """
        return self.topic

    def get_meta(self) -> {}:
        """
        Retrieve the metadata of the feed.
        Returns:
            {}: The metadata of the feed.
        """
        return self.meta


class FeedStation:
    """
    FeedStation class to store feeds for various topics.
    """

    def __init__(self) -> None:
        """Initialize a FeedStation with an empty feed dictionary."""
        self.feed_dict: dict[str, Feed] = {}

    def add_feed(self, key: str, feed: Feed) -> None:
        """Add a feed to the feed dictionary.

        Args:
            key (str): The key for the feed.
            feed (Feed): The feed object to add.
        """
        self.feed_dict[key] = feed

    def get_feed(self, key: str) -> Optional[Feed]:
        """Retrieve a feed from the feed dictionary.

        Args:
            key (str): The key for the feed.

        Returns:
            Optional[Feed]: The feed object if found, else None.
        """
        return self.feed_dict.get(key)

    def remove_feed(self, key: str) -> None:
        """Remove a feed from the feed dictionary.

        Args:
            key (str): The key for the feed to remove.
        """
        self.feed_dict.pop(key, None)


class GrowwFeed:
    """
    Used to subscribe to the Groww feed and get the feed data.

    One instance of GrowwFeed can be used to subscribe to multiple topics and get the feed data.

    Note:
        Only one subscription can be created to each topic by a single instance of GrowwFeed.
    """

    _GROWW_SOCKET_URL: Final[str] = "wss://socket-api.groww.in"
    _GROWW_GENERATE_SOCKET_TOKEN_URL: Final[str] = "https://api.groww.in/v1/api/apex/v1/socket/token/create/"
    _nats_clients: dict[tuple[str, str], NatsClient] = {}


    def __init__(self, groww_api: GrowwAPI) -> None:
        """
        Initialize the GrowwFeed class with socket token and key.

        Args:
            groww_api (GrowwAPI): The Groww API instance.

        Raises:
            GrowwFeedConnectionException: If the socket connection fails.
        """
        self.groww_api = groww_api
        self._feed_station: FeedStation = FeedStation()
        self._client_key, self.subscription_id = self._key()
        if self._client_key not in GrowwFeed._nats_clients:
            GrowwFeed._nats_clients[self._client_key] = NatsClient(
                GrowwFeed._GROWW_SOCKET_URL,
                self._client_key[0],
                self._client_key[1],
                self._update_feed_data,
            )
        self._nats_client = GrowwFeed._nats_clients[self._client_key]
    
    def _key(self):
        seed = os.urandom(32)
        signing_key = nacl.signing.SigningKey(seed)
        key_pair = nkeys.KeyPair(keys=signing_key, seed=nkeys.encode_seed(seed, nkeys.PREFIX_BYTE_USER))
        socket_token_response = self.groww_api.generate_socket_token(
            key_pair)
        socket_jwt_token = socket_token_response["token"]
        subscription_id = socket_token_response["subscriptionId"]
        client_key = (
            socket_jwt_token,
            key_pair.seed.decode("utf-8")
        )
        return client_key, subscription_id

    
    def subscribe_fno_position_updates(
        self,
        on_data_received: Optional[Callable[[], None]] = None,
    ) -> bool:
        """
        Subscribe to the position updates of a derivatives contract.

        Subscription can be created only once for a given feed key.

        Args:
            on_data_received (Optional[Callable[[], None]]): The callback function to call on data update.

        Returns:
            bool: True if a new subscription was created, False otherwise.
        """
        topic = FeedConstants.get_derivatives_position_updates_topic(self.subscription_id)
        return self._subscribe(
            topic.get_topic(),
            on_data_received,
            topic.get_meta()
        )

    def unsubscribe_fno_position_updates(
        self,
    ) -> bool:
        """
        Unsubscribe from the position updates of a derivatives contract.

        Returns:
            bool: True if existing subscription was unsubscribed, False otherwise.
        """
        topic = FeedConstants.get_derivatives_position_updates_topic(self.subscription_id)
        return self._unsubscribe(topic.get_topic())

    def subscribe_fno_order_updates(
        self,
        on_data_received: Optional[Callable[[], None]] = None,
    ) -> bool:
        """
        Subscribe to the order updates of a derivatives contract.

        Subscription can be created only once for a given feed key.

        Args:
            on_data_received (Optional[Callable[[], None]]): The callback function to call on data update.

        Returns:
            bool: True if a new subscription was created, False otherwise.
        """
        topic = FeedConstants.get_derivatives_order_updates_topic(self.subscription_id)
        return self._subscribe(
            topic.get_topic(),
            on_data_received,
            topic.get_meta()
        )

    def subscribe_equity_order_updates(
        self,
        on_data_received: Optional[Callable[[], None]] = None,
    ) -> bool:
        """
        Subscribe to the order updates of a stock.

        Subscription can be created only once for a given feed key.

        Args:
            on_data_received (Optional[Callable[[], None]]): The callback function to call on data update.

        Returns:
            bool: True if a new subscription was created, False otherwise.
        """
        topic = FeedConstants.get_equity_order_updates_topic(self.subscription_id)
        return self._subscribe(
            topic.get_topic(),
            on_data_received,
            topic.get_meta()
        )

    def unsubscribe_equity_order_updates(self) -> bool:
        """
        Unsubscribe from the order updates of a stock.

        Returns:
            bool: True if existing subscription was unsubscribed, False otherwise.
        """
        topic = FeedConstants.get_equity_order_updates_topic(self.subscription_id)
        return self._unsubscribe(topic.get_topic())

    def unsubscribe_fno_order_updates(self) -> bool:
        """
        Unsubscribe from the order updates of a stock.

        Returns:
            bool: True if existing subscription was unsubscribed, False otherwise.
        """
        topic = FeedConstants.get_derivatives_order_updates_topic(self.subscription_id)
        return self._unsubscribe(topic.get_topic())

    def get_fno_order_update(
        self
    ) -> Optional[dict[str, any]]:
        """
        Get the order updates of a derivatives contract.

        Returns:
            Optional[dict[str, any]]: The order details, or None if data is not available.

        Raises:
            GrowwFeedNotSubscribedException: If the feed was not subscribed before attempting to get.
        """
        topic = FeedConstants.get_derivatives_order_updates_topic(self.subscription_id)
        feed = self._feed_station.get_feed(topic.get_topic())
        data = feed.get_data() if feed else None
        return get_data_dict(data, FeedConstants.ORDER_UPDATE)

    def get_fno_position_update(
        self
    ) -> Optional[dict[str, any]]:
        """
        Get the position updates of a derivatives contract.

        Returns:
            Optional[dict[str, any]]: The exchange wise position, or None if data is not available.

        Raises:
            GrowwFeedNotSubscribedException: If the feed was not subscribed before attempting to get.
        """
        topic = FeedConstants.get_derivatives_position_updates_topic(self.subscription_id)
        feed = self._feed_station.get_feed(topic.get_topic())
        data = feed.get_data() if feed else None
        return get_data_dict(data, FeedConstants.POSITION_UPDATE)

    def get_equity_order_update(
        self
    ) -> Optional[dict[str, any]]:
        """
        Get the order updates of a stock.

        Returns:
            Optional[dict[str, any]]: The order details, or None if data is not available.

        Raises:
            GrowwFeedNotSubscribedException: If the feed was not subscribed before attempting to get.
        """
        topic = FeedConstants.get_equity_order_updates_topic(self.subscription_id)
        feed = self._feed_station.get_feed(topic.get_topic())
        data = feed.get_data() if feed else None
        return get_data_dict(data, FeedConstants.ORDER_UPDATE)

    def _update_feed_data(self, topic: str, data: any) -> None:
        """
        Update the feed data for a given topic.

        Args:
            topic (str): The feed key.
            data (any): The data to update.
        """
        self._feed_station.get_feed(topic).update(data)

    def _subscribe(
        self,
        topic: str,
        on_data_received: Optional[Callable[[], None]] = None,
        meta: Optional[{}] = None,
    ) -> bool:
        """
        Subscribe to a given topic.

        Subscription can be created only once for a given topic.

        Args:
            topic (str): The topic to subscribe to.
            on_data_received (Optional[Callable[[], None]]): The callback function to call on data update.

        Returns:
            bool: True if a new subscription was created, False otherwise.
        """
        if self._nats_client.is_subscribed(topic):
            return False

        self._feed_station.add_feed(topic, Feed(topic, on_data_received, meta))
        return self._nats_client.subscribe_topic(topic)

    def _unsubscribe(self, topic: str) -> bool:
        """
        Unsubscribe from a given topic.

        Args:
            topic (str): The topic to unsubscribe from.

        Returns:
            bool: True if unsubscribed, else False.
        """
        if not self._nats_client.is_subscribed(topic):
            return False

        is_unsubed: bool = self._nats_client.unsubscribe_topic(topic)
        self._feed_station.remove_feed(topic)
        return is_unsubed

    def _unsubscribe_topics(self, topics: list[FeedConstants.Topic]) -> dict[str, bool]:
        """
        Unsubscribe from a given topic.

        Args:
            topics (list): List of topics to unsubscribe from.

        Returns:
            bool: True if unsubscribed, else False.
        """
        resp = {}
        for topic in topics:
            resp.setdefault(topic.get_meta()[FeedConstants.EXCHANGE], {}).setdefault(
                topic.get_meta()[FeedConstants.SEGMENT], {})[topic.get_meta()[FeedConstants.FEED_KEY]] \
                = self._unsubscribe(topic.get_topic())
        return resp


    def subscribe_ltp(self, instrument_list: list[dict[str, any]], on_data_received: Optional[Callable[[], None]] = None, ) ->dict[Any, dict[Any, dict[Any, dict[str, Any] | None]]] | None:
        """
        Get the last traded price (LTP) of a list of instruments.

        Args:
            instrument_list (list[dict[str]]): List of dictionaries containing instrument details.

        Returns:
            dict[str, float]: Dictionary with instrument keys and their corresponding LTP values.
        """
        self._assert_valid_instruments(instrument_list)
        topics = self._get_topics(instrument_list, FeedConstants.get_live_price_topic)
        return self._subscribe_topics(topics, on_data_received)

    def unsubscribe_ltp(self, instrument_list: list[dict[str, any]]) -> dict[str, bool]:
        """
        Unsubscribe from the last traded price (LTP) of a list of instruments.

        Args:
            instrument_list (list[dict[str]]): List of dictionaries containing instrument details.

        Returns:
            dict[str, bool]: Dictionary with instrument keys and their corresponding unsubscribe status.
        """
        self._assert_valid_instruments(instrument_list)
        topics = self._get_topics(instrument_list, FeedConstants.get_live_price_topic)
        return self._unsubscribe_topics(topics)

    def subscribe_market_depth(self, instrument_list: list[dict[str, any]], on_data_received: Optional[Callable[[], None]] = None, ) ->dict[Any, dict[Any, dict[Any, dict[str, Any] | None]]] | None:
        """
        Get the last traded price (LTP) of a list of instruments.

        Args:
            instrument_list (list[dict[str]]): List of dictionaries containing instrument details.

        Returns:
            dict[str, float]: Dictionary with instrument keys and their corresponding LTP values.
        """
        self._assert_valid_instruments(instrument_list)
        topics = self._get_topics(instrument_list,
                                  FeedConstants.get_market_depth)
        return self._subscribe_topics(topics, on_data_received)

    def unsubscribe_market_depth(self, instrument_list: list[dict[str, any]]) -> dict[str, bool]:
        """
        Unsubscribe from the last traded price (LTP) of a list of instruments.

        Args:
            instrument_list (list[dict[str, any]]): List of dictionaries containing instrument details.

        Returns:
            dict[str, bool]: Dictionary with instrument keys and their corresponding unsubscribe status.
        """
        self._assert_valid_instruments(instrument_list)
        topics = self._get_topics(instrument_list,
                                  FeedConstants.get_market_depth)
        return self._unsubscribe_topics(topics)

    def subscribe_index_value(self, instrument_list: list[dict[str, any]], on_data_received: Optional[Callable[[], None]] = None, ) ->dict:
        """
        Get the last traded price (LTP) of a list of instruments.

        Args:
            instrument_list (list[dict[str, any]]): List of dictionaries containing instrument details.

        Returns:
            dict[str, float]: Dictionary with instrument keys and their corresponding LTP values.
        """
        self._assert_valid_instruments(instrument_list)
        topics = self._get_topics(instrument_list,
                                  FeedConstants.get_live_index_topic)
        return self._subscribe_topics(topics, on_data_received)

    def unsubscribe_index_value(self, instrument_list: list[dict[str, any]]) -> dict[str, bool]:
        """
        Unsubscribe from the last traded price (LTP) of a list of instruments.

        Args:
            instrument_list (list[dict[str, any]]): List of dictionaries containing instrument details.

        Returns:
            dict[str, bool]: Dictionary with instrument keys and their corresponding unsubscribe status.
        """
        self._assert_valid_instruments(instrument_list)
        topics = self._get_topics(instrument_list,
                                  FeedConstants.get_live_index_topic)
        return self._unsubscribe_topics(topics)


    def _subscribe_topics(self,topics: list[FeedConstants.Topic], on_data_received: Optional[any]) -> dict:
        resp = {}
        for topic in topics:
            resp.setdefault(topic.get_meta()[FeedConstants.EXCHANGE], {}).setdefault(
                topic.get_meta()[FeedConstants.SEGMENT], {})[topic.get_meta()[FeedConstants.FEED_KEY]] \
                = self._subscribe(topic.get_topic(), on_data_received, topic.get_meta())
        return resp

    def _fetch(self, feed: Feed):
        return feed.get_meta(), feed.get_data()

    def _get_feed(self, topics: list[str]):
        """
            Get the feed for a list of topics.
        Args:
            topics (list[str]): List of topics to fetch data for.
        Returns:
            dict: Dictionary containing the feed data for each topic.
        """
        feeds = [self._feed_station.get_feed(topic) for topic in topics]
        feeds = [feed for feed in feeds if feed is not None]
        feed_topics = {feed.get_topic() for feed in feeds}
        desired_topic = set(topics)
        if not desired_topic.issubset(feed_topics):
            raise GrowwFeedNotSubscribedException(
                "Feed not subscribed for topic! A subscription is required to get the data.",
                "Topics")
        results = [self._fetch(feed) for feed in feeds]
        resp = {}
        for meta, data in (result for result in results):
            self._generate_feed_response(data, meta, resp)
        return resp

    def get_all_feed(self):
        """
            Get the feed for all topics.
        Args:
        Returns:
            dict: Dictionary containing the feed data for each topic.
        """
        topics = self._feed_station.feed_dict.keys()
        return self._get_feed(list(topics))

    def get_ltp(self) -> dict:
        """
        Get the last traded price (LTP) of all subscribed instruments

        Args:
        Returns:
            dict[str, float]: Dictionary with instrument keys and their corresponding LTP values.
        """
        feeds = self._feed_station.feed_dict.values()
        live_data_feed = list(filter(lambda x: x.get_meta()[FeedConstants.FEED_TYPE]
                                               == FeedConstants.LIVE_DATA,
                                     feeds))
        topics = [topic.get_topic() for topic in live_data_feed]
        return self._get_feed(list(topics))[FeedConstants.LIVE_DATA]

    def get_index_value(self) -> dict:
        """
        Get the index value of all subscribed instruments.

        Args:
        Returns:
            dict[str, float]: Dictionary with instrument keys and their corresponding index values.
        """
        feeds = self._feed_station.feed_dict.values()
        live_data_feed = list(filter(lambda x: x.get_meta()[FeedConstants.FEED_TYPE]
                                               == FeedConstants.LIVE_INDEX,
                                     feeds))
        topics = [topic.get_topic() for topic in live_data_feed]
        return self._get_feed(list(topics))[FeedConstants.LIVE_INDEX]


    def get_market_depth(self) -> dict:
        """
            Get the market depth  for all subscribed instruments.

        Args:
        Returns:
            dict[str, float]: Market Feed response dictionary
        """
        feeds = self._feed_station.feed_dict.values()
        live_data_feed = list(filter(lambda x: x.get_meta()[FeedConstants.FEED_TYPE]
                                               == FeedConstants.MARKET_DEPTH,
                                     feeds))
        topics = [topic.get_topic() for topic in live_data_feed]
        return self._get_feed(list(topics))[FeedConstants.MARKET_DEPTH]

    def _generate_feed_response(self, data, meta, resp: {}):
        """
        Generate the feed response for a given data and metadata.

        Args:
            data (any): The data to generate the response for.
            meta (dict[str, any]): The metadata for the data.

        Returns:
            dict[str, any]: The generated feed response.
        """
        if not meta or not meta.get(FeedConstants.FEED_TYPE):
            return {}

        feed_type = meta[FeedConstants.FEED_TYPE]
        exchange = meta.get(FeedConstants.EXCHANGE)
        segment = meta.get(FeedConstants.SEGMENT)
        feed_key = meta.get(FeedConstants.FEED_KEY)

        if FeedConstants.ORDER_UPDATE == feed_type or FeedConstants.POSITION_UPDATE == feed_type:
            resp.setdefault(feed_type, {})[segment] = get_data_dict(data, feed_type)
        else:
            resp.setdefault(feed_type, {}).setdefault(
                exchange, {}).setdefault(segment, {})[feed_key] = get_data_dict(data, feed_type)


    def _get_topics(self, instrument_list: list[dict[str, str]], topic_function) -> list[FeedConstants.Topic]:
        """
        Get the topics for the given instrument list.

        Args:
            instrument_list (list[dict[str]]): List of dictionaries containing instrument details.

        Returns:
            list[Topic]: List of topics.
        """
        topics= []
        for instrument in instrument_list:
            if not isinstance(instrument, dict):
                raise TypeError("Each instrument must be a dictionary.")

            exchange = instrument.get(FeedConstants.EXCHANGE)
            segment = instrument.get(FeedConstants.SEGMENT)
            token = instrument.get(FeedConstants.EXCHANGE_TOKEN)

            if not exchange or not segment or not token:
                raise ValueError("Check if exchange, segment and token are provided in the instrument dictionary.")

            topics += [topic_function(segment, exchange, token)]
        return topics

    def _assert_valid_instruments(self, instrument_list: list[dict[str, str]]) -> None:
        """
        Assert the instruments in the list.

        Args:
            instrument_list (list[dict[str]]): List of dictionaries containing instrument details.

        Raises:
            TypeError: If the instrument list is not a list of dictionaries.
            ValueError: If the instrument list is empty or if any required keys are missing.
        """
        if not isinstance(instrument_list, list) or not all(isinstance(i, dict) for i in instrument_list):
            raise TypeError("instrument_list must be a list of dictionaries.")
        if not instrument_list:
            raise ValueError("At least one instrument must be provided")

    def consume(self):
        self._nats_client.consume_thread.join()