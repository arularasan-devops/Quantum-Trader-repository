"""
This module provides artifacts for connecting to NATS servers and subscribing to topics.
"""

import asyncio
import logging
import threading
import ssl
import certifi
from typing import Callable

from nats import connect
from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription

from growwapi.groww.exceptions import GrowwFeedConnectionException
from growwapi.common import files as file_utils

logger = logging.getLogger(__name__)


class NatsClient:
    """
    A client for connecting to NATS server and subscribing to topics.
    """

    def __init__(
        self,
        socket_url: str,
        nats_token: str,
        nkey_seed: str,
        callback: Callable[[str, any], None],
    ) -> None:
        """
        Initialize the NatsClient.

        Args:
            socket_url (str): The NATS server URL.
            nats_token (str): The NATS token.
            nkey_seed (str): The NATS key seed.
            callback (Callable[[str, any], None]): The callback function to handle messages.
        
        Raises:
            GrowwFeedConnectionException: If the socket connection fails.
        """
        self.socket_url: str = socket_url
        self.nats_token: str = nats_token
        self.nkey_seed: str = nkey_seed
        self.callback: Callable[[str, any], None] = callback
        self._socket: Client = None
        self._subscriptions: dict[str, Subscription] = {}
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._connect())
        self.consume_thread = self._consume_async()

    def subscribe_topic(self, topic: str) -> bool:
        """
        Subscribe to a topic if not already subscribed.

        Args:
            topic (str): The topic to subscribe to.

        Returns:
            bool: True if a new subscription was created, False otherwise.
        """
        if self.is_subscribed(topic):
            return False
        asyncio.run_coroutine_threadsafe(self._subscribe(topic), self._loop)
        return True

    def unsubscribe_topic(self, topic: str) -> bool:
        """
        Unsubscribe from a topic if already subscribed.

        Args:
            topic (str): The topic to unsubscribe from.

        Returns:
            bool: True if existing subscription was unsubscribed, False otherwise
        """
        if not self.is_subscribed(topic):
            return False

        asyncio.run_coroutine_threadsafe(self._unsubscribe(topic), self._loop)
        return True

    def is_subscribed(self, topic: str) -> bool:
        """
        Check if subscribed to a topic.

        Args:
            topic (str): The topic to check.

        Returns:
            bool: True if subscribed, False otherwise.
        """
        return self._subscriptions.get(topic) is not None

    def _consume(self) -> None:
        """
        Start the event loop to consume messages.
        """
        try:
            self._loop.run_forever()
        except KeyboardInterrupt:
            asyncio.run(self._socket.close())
            self._loop.stop()
            self._loop.close()

    async def _connect(self) -> None:
        """
        Connect to the NATS server.

        Raises:
            GrowwFeedConnectionException: If the socket connection fails.
        """
        token_path: str = file_utils.generate_token_file(self.nats_token)
        seed_path: str = file_utils.generate_seed_file(self.nkey_seed)
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._socket = await connect(
            servers=self.socket_url,
            user_credentials=(token_path, seed_path),
            closed_cb=self._on_closed_cb,
            disconnected_cb=self._on_disconnected_cb,
            error_cb=self._on_error_cb,
            reconnected_cb=self._on_reconnected_cb,
            tls=ssl_context,
            ping_interval=60,
        )
        if self._socket.is_connected:
            logger.info("Socket connection successful")
        else:
            raise GrowwFeedConnectionException("Socket connection failed")

    def _consume_async(self) -> threading.Thread:
        """
        Start the event loop in a separate thread.
        """
        p_thread: threading.Thread = threading.Thread(target=self._consume, daemon=True)
        p_thread.start()
        return p_thread

    async def _on_closed_cb(self):
        logger.info("Connection closed")

    async def _on_disconnected_cb(self):
        logger.warning("Disconnected")

    async def _on_error_cb(self, e):
        logger.error("Error: %s", e)

    async def _on_reconnected_cb(self):
        logger.info("Reconnected")

    async def _subscribe(self, topic: str) -> bool:
        """
        Subscribe to a topic.

        Args:
            topic (str): The topic to subscribe to.

        Returns:
            bool: True if subscription was attempted successfully, False otherwise.
        """
        sub: Subscription = await self._socket.subscribe(
            subject=topic,
            cb=self._on_data_cb,
        )
        await self._socket.flush(10)
        self._subscriptions.update({topic: sub})
        return True

    async def _unsubscribe(self, topic: str) -> bool:
        """
        Unsubscribe from a topic.

        Args:
            topic (str): The topic to unsubscribe from.

        Returns:
            bool: True if unsubscription was successful, False
        """
        sub: Subscription = self._subscriptions.get(topic)
        await sub.unsubscribe()
        await self._socket.flush(10)
        self._subscriptions.pop(topic)
        return True

    async def _on_data_cb(self, msg: Msg) -> None:
        """
        Callback function to handle incoming messages.

        Args:
            msg (Msg): The incoming message.
        """
        self.callback(msg.subject, msg.data)
