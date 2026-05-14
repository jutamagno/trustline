from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from elasticsearch import Elasticsearch, helpers

from trustline.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_es_client() -> Elasticsearch:
    settings = get_settings()
    return Elasticsearch(settings.es_url, request_timeout=10)


EVENTS_MAPPING = {
    "mappings": {
        "properties": {
            "event_id": {"type": "keyword"},
            "correspondent_id": {"type": "keyword"},
            "channel": {"type": "keyword"},
            "product_type": {"type": "keyword"},
            "risk_level": {"type": "keyword"},
            "risk_score": {"type": "float"},
            "occurred_at": {"type": "date"},
            "region": {"type": "keyword"},
            "loan_amount": {"type": "float"},
            "inconsistencies": {"type": "text"},
            "llm_reasoning": {"type": "text"},
        }
    }
}

AUDIT_MAPPING = {
    "mappings": {
        "properties": {
            "entry_id": {"type": "keyword"},
            "event_id": {"type": "keyword"},
            "correspondent_id": {"type": "keyword"},
            "decision": {"type": "keyword"},
            "risk_level": {"type": "keyword"},
            "flags": {"type": "text"},
            "decided_at": {"type": "date"},
        }
    }
}


class ElasticClient:
    def __init__(self, es: Elasticsearch | None = None) -> None:
        settings = get_settings()
        self._es = es or get_es_client()
        self._events_idx = settings.es_index_events
        self._audit_idx = settings.es_index_audit

    def ensure_indexes(self) -> None:
        for idx, mapping in (
            (self._events_idx, EVENTS_MAPPING),
            (self._audit_idx, AUDIT_MAPPING),
        ):
            if not self._es.indices.exists(index=idx):
                self._es.indices.create(index=idx, body=mapping)
                logger.info("es_index_created", extra={"index": idx})

    def index_event(self, event_doc: dict, analysis_doc: dict | None = None) -> None:
        doc = {
            "event_id": event_doc.get("event_id"),
            "correspondent_id": event_doc.get("correspondent_id"),
            "channel": event_doc.get("channel"),
            "product_type": event_doc.get("product_type"),
            "occurred_at": event_doc.get("occurred_at"),
            "region": event_doc.get("region", ""),
            "loan_amount": event_doc.get("loan_amount", 0.0),
        }
        if analysis_doc:
            doc["risk_level"] = analysis_doc.get("risk_level")
            doc["risk_score"] = analysis_doc.get("risk_score", 0.0)
            doc["inconsistencies"] = " | ".join(analysis_doc.get("inconsistencies", []))
            doc["llm_reasoning"] = analysis_doc.get("llm_reasoning", "")

        self._es.index(index=self._events_idx, id=doc["event_id"], document=doc)

    def index_audit_entry(self, entry: dict) -> None:
        self._es.index(
            index=self._audit_idx,
            id=entry.get("entry_id"),
            document=entry,
        )

    def search_events(self, query_body: dict, size: int = 20) -> list[dict[str, Any]]:
        resp = self._es.search(index=self._events_idx, body=query_body, size=size)
        return [h["_source"] for h in resp["hits"]["hits"]]

    def search_audit(self, query_body: dict, size: int = 20) -> list[dict[str, Any]]:
        resp = self._es.search(index=self._audit_idx, body=query_body, size=size)
        return [h["_source"] for h in resp["hits"]["hits"]]

    def natural_language_to_query(self, nl_query: str, llm_client=None) -> dict:
        """Convert NL query to ES query using LLM, fall back to multi_match."""
        if llm_client:
            try:
                from trustline.llm.prompts import nl_to_es_query_prompt
                prompt = nl_to_es_query_prompt(nl_query)
                result = llm_client.invoke_json(prompt, max_tokens=512)
                if result and "query" in result:
                    return result
            except Exception as exc:
                logger.warning("nl_to_es_fallback", extra={"error": str(exc)})

        return {
            "query": {
                "multi_match": {
                    "query": nl_query,
                    "fields": [
                        "correspondent_id", "channel", "product_type",
                        "risk_level", "inconsistencies", "llm_reasoning",
                    ],
                }
            }
        }

    def ping(self) -> bool:
        try:
            return self._es.ping()
        except Exception:
            return False
