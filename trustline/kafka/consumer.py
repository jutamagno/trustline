from __future__ import annotations

import json
import logging
from typing import Callable

from kafka import KafkaConsumer

from trustline.config import get_settings
from trustline.models import OriginationEvent

logger = logging.getLogger(__name__)


class OriginationConsumer:
    """Kafka consumer that feeds OriginationEvents to a handler."""

    def __init__(
        self,
        handler: Callable[[OriginationEvent], None],
        group_id: str = "trustline-analyzer",
        brokers: str | None = None,
        topic: str | None = None,
    ) -> None:
        settings = get_settings()
        self._handler = handler
        self._topic = topic or settings.origination_topic
        self._consumer = KafkaConsumer(
            self._topic,
            bootstrap_servers=(brokers or settings.kafka_brokers).split(","),
            group_id=group_id,
            value_deserializer=lambda v: json.loads(v.decode()),
            auto_offset_reset="earliest",
            enable_auto_commit=True,
        )
        self._running = False

    def run(self) -> None:
        self._running = True
        logger.info("consumer_started", extra={"topic": self._topic})
        try:
            for message in self._consumer:
                if not self._running:
                    break
                try:
                    event = OriginationEvent.from_doc(message.value)
                    self._handler(event)
                except Exception as exc:
                    logger.error(
                        "consumer_handler_error",
                        extra={"offset": message.offset, "error": str(exc)},
                    )
        finally:
            self._consumer.close()
            logger.info("consumer_stopped")

    def close(self) -> None:
        self._running = False
