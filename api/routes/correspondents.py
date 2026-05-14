from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_event_store
from trustline.db.mongo import EventStore

router = APIRouter()


@router.get("")
def list_correspondents(
    risk_level: str | None = Query(None, description="Filter: low|medium|high|critical"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    store: EventStore = Depends(get_event_store),
) -> dict:
    scores = store.list_risk_scores(risk_level=risk_level, limit=limit, offset=offset)
    return {"correspondents": scores, "count": len(scores)}


@router.get("/{correspondent_id}/risk")
def get_correspondent_risk(
    correspondent_id: str,
    store: EventStore = Depends(get_event_store),
) -> dict:
    score = store.get_risk_score(correspondent_id)
    if not score:
        raise HTTPException(status_code=404, detail="Correspondent not found or not yet scored")
    results_30d = store.get_correspondent_results(correspondent_id, days=30)
    flagged = [r for r in results_30d if r.get("risk_level") in ("high", "critical")]
    return {
        "risk_score": score,
        "recent_flagged_events": flagged[:10],
        "total_flagged_30d": len(flagged),
    }


@router.get("/{correspondent_id}/events")
def get_correspondent_events(
    correspondent_id: str,
    days: int = Query(30, ge=1, le=90),
    limit: int = Query(100, ge=1, le=500),
    store: EventStore = Depends(get_event_store),
) -> dict:
    events = store.get_correspondent_events(correspondent_id, days=days, limit=limit)
    if not events:
        raise HTTPException(status_code=404, detail="No events found for this correspondent")
    return {"correspondent_id": correspondent_id, "events": events, "count": len(events)}
