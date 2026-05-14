from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import pymongo
from pymongo import MongoClient
from pymongo.collection import Collection

from trustline.config import get_settings
from trustline.models import AnalysisResult, CorrespondentRiskScore, OriginationEvent

logger = logging.getLogger(__name__)


def _get_client() -> MongoClient:
    settings = get_settings()
    return MongoClient(settings.mongo_uri)


@lru_cache(maxsize=1)
def get_db():
    settings = get_settings()
    client = _get_client()
    return client[settings.mongo_db]


class EventStore:
    """Append-only MongoDB-backed store for origination events."""

    def __init__(self, db=None) -> None:
        self._db = db or get_db()
        self._events: Collection = self._db["origination_events"]
        self._results: Collection = self._db["analysis_results"]
        self._risk_scores: Collection = self._db["correspondent_risk_scores"]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        settings = get_settings()
        ttl_seconds = settings.event_ttl_days * 86_400

        self._events.create_index("event_id", unique=True)
        self._events.create_index("correspondent_id")
        self._events.create_index("occurred_at")
        self._events.create_index(
            "occurred_at", expireAfterSeconds=ttl_seconds, name="ttl_idx"
        )

        self._results.create_index("event_id", unique=True)
        self._results.create_index("correspondent_id")
        self._results.create_index("analyzed_at")
        self._results.create_index("risk_level")

        self._risk_scores.create_index("correspondent_id", unique=True)
        self._risk_scores.create_index("risk_level")

    def append_event(self, event: OriginationEvent) -> bool:
        """Returns True if inserted, False if already exists (idempotent)."""
        doc = event.to_doc()
        doc["_inserted_at"] = datetime.now(UTC)
        try:
            self._events.insert_one(doc)
            logger.info("event_stored", extra={"event_id": event.event_id})
            return True
        except pymongo.errors.DuplicateKeyError:
            logger.warning("duplicate_event", extra={"event_id": event.event_id})
            return False

    def save_analysis(self, result: AnalysisResult) -> None:
        doc = result.to_doc()
        doc["_inserted_at"] = datetime.now(UTC)
        self._results.replace_one(
            {"event_id": result.event_id}, doc, upsert=True
        )

    def save_risk_score(self, score: CorrespondentRiskScore) -> None:
        doc = score.to_doc()
        doc["_updated_at"] = datetime.now(UTC)
        self._risk_scores.replace_one(
            {"correspondent_id": score.correspondent_id}, doc, upsert=True
        )

    def get_event(self, event_id: str) -> dict | None:
        return self._events.find_one({"event_id": event_id}, {"_id": 0})

    def get_analysis(self, event_id: str) -> dict | None:
        return self._results.find_one({"event_id": event_id}, {"_id": 0})

    def get_risk_score(self, correspondent_id: str) -> dict | None:
        return self._risk_scores.find_one(
            {"correspondent_id": correspondent_id}, {"_id": 0}
        )

    def list_risk_scores(
        self,
        risk_level: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        query: dict[str, Any] = {}
        if risk_level:
            query["risk_level"] = risk_level
        cursor = (
            self._risk_scores.find(query, {"_id": 0})
            .sort("score", pymongo.DESCENDING)
            .skip(offset)
            .limit(limit)
        )
        return list(cursor)

    def get_correspondent_events(
        self,
        correspondent_id: str,
        days: int = 30,
        limit: int = 200,
    ) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        cursor = (
            self._events.find(
                {
                    "correspondent_id": correspondent_id,
                    "occurred_at": {"$gte": since.isoformat()},
                },
                {"_id": 0},
            )
            .sort("occurred_at", pymongo.DESCENDING)
            .limit(limit)
        )
        return list(cursor)

    def get_correspondent_results(
        self,
        correspondent_id: str,
        days: int = 30,
    ) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        cursor = self._results.find(
            {
                "correspondent_id": correspondent_id,
                "analyzed_at": {"$gte": since.isoformat()},
            },
            {"_id": 0},
        ).sort("analyzed_at", pymongo.DESCENDING)
        return list(cursor)

    def active_correspondent_ids(self, days: int = 30) -> list[str]:
        since = datetime.now(UTC) - timedelta(days=days)
        return self._events.distinct(
            "correspondent_id",
            {"occurred_at": {"$gte": since.isoformat()}},
        )

    def aggregate_stats(self, days: int = 30) -> dict:
        since = datetime.now(UTC) - timedelta(days=days)
        total = self._events.count_documents(
            {"occurred_at": {"$gte": since.isoformat()}}
        )
        flagged = self._results.count_documents(
            {
                "analyzed_at": {"$gte": since.isoformat()},
                "risk_level": {"$in": ["high", "critical"]},
            }
        )
        blocked = self._results.count_documents(
            {
                "analyzed_at": {"$gte": since.isoformat()},
                "risk_level": "critical",
            }
        )
        risk_dist = {}
        for level in ["low", "medium", "high", "critical"]:
            risk_dist[level] = self._risk_scores.count_documents({"risk_level": level})
        return {
            "total_events": total,
            "flagged": flagged,
            "blocked": blocked,
            "detection_rate": round(flagged / total, 4) if total else 0.0,
            "risk_distribution": risk_dist,
        }

    def get_consent_inventory(self) -> list[dict]:
        """LGPD: unique cpf_hash + product combinations with consent info."""
        pipeline = [
            {
                "$group": {
                    "_id": {
                        "cpf_hash": "$customer_cpf_hash",
                        "product_type": "$product_type",
                    },
                    "consent_method": {"$last": "$consent_method"},
                    "channel": {"$last": "$channel"},
                    "correspondent_id": {"$last": "$correspondent_id"},
                    "last_seen": {"$max": "$occurred_at"},
                    "count": {"$sum": 1},
                }
            },
            {"$project": {"_id": 0, "cpf_hash": "$_id.cpf_hash",
                          "product_type": "$_id.product_type",
                          "consent_method": 1, "channel": 1,
                          "correspondent_id": 1, "last_seen": 1, "count": 1}},
        ]
        return list(self._events.aggregate(pipeline))
