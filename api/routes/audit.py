from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from api.deps import get_event_store
from trustline.db.postgres import query_audit_trail

router = APIRouter()


@router.get("/trail")
def get_audit_trail(
    correspondent_id: str | None = Query(None),
    from_dt: str | None = Query(None, description="ISO 8601"),
    to_dt: str | None = Query(None, description="ISO 8601"),
    risk_level: str | None = Query(None, description="low|medium|high|critical"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    from_parsed = datetime.fromisoformat(from_dt) if from_dt else None
    to_parsed = datetime.fromisoformat(to_dt) if to_dt else None

    try:
        entries = query_audit_trail(
            correspondent_id=correspondent_id,
            from_dt=from_parsed,
            to_dt=to_parsed,
            risk_level=risk_level,
            limit=limit,
            offset=offset,
        )
    except Exception:
        entries = []

    return {"entries": entries, "count": len(entries)}
