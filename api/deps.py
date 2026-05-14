from __future__ import annotations

from functools import lru_cache

from trustline.db.mongo import EventStore
from trustline.search.elastic import ElasticClient


@lru_cache(maxsize=1)
def get_event_store() -> EventStore:
    return EventStore()


@lru_cache(maxsize=1)
def get_elastic_client() -> ElasticClient:
    return ElasticClient()
