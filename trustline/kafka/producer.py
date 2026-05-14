from __future__ import annotations

import json
import logging
from functools import lru_cache

from kafka import KafkaProducer

from trustline.config import get_settings
from trustline.models import AnalysisResult, OriginationEvent

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_producer() -> KafkaProducer:
    settings = get_settings()
    return KafkaProducer(
        bootstrap_servers=settings.kafka_brokers.split(","),
        value_serializer=lambda v: json.dumps(v, default=str).encode(),
        acks="all",
        retries=3,
    )


def publish_origination(event: OriginationEvent) -> None:
    settings = get_settings()
    try:
        _get_producer().send(settings.origination_topic, value=event.to_doc())
        logger.info("event_published", extra={"event_id": event.event_id})
    except Exception as exc:
        logger.error("publish_failed", extra={"event_id": event.event_id, "error": str(exc)})
        raise


def publish_analysis(result: AnalysisResult) -> None:
    settings = get_settings()
    try:
        _get_producer().send(settings.analysis_topic, value=result.to_doc())
    except Exception as exc:
        logger.error("analysis_publish_failed", extra={"event_id": result.event_id, "error": str(exc)})
        raise


def publish_alert(topic: str, payload: dict) -> None:
    try:
        _get_producer().send(topic, value=payload)
    except Exception as exc:
        logger.error("alert_publish_failed", extra={"topic": topic, "error": str(exc)})
        raise
