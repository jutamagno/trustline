from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_event_store
from trustline.analyzers.inconsistency import InconsistencyDetector
from trustline.analyzers.consent_verifier import ConsentVerifier
from trustline.db.mongo import EventStore
from trustline.db.postgres import insert_audit_entry
from trustline.models import (
    AnalysisResult,
    AuditEntry,
    Channel,
    ConsentMethod,
    OriginationEvent,
    ProductType,
    RiskLevel,
    hash_cpf,
)

router = APIRouter()


class OriginationEventIn(BaseModel):
    correspondent_id: str
    channel: Channel
    product_type: ProductType
    customer_cpf: str = Field(..., description="CPF do cliente — armazenado como hash SHA-256")
    customer_age: int = Field(..., ge=18, le=90)
    loan_amount: float = Field(..., gt=0)
    contract_date: str = Field(..., description="ISO 8601 date")
    consent_method: ConsentMethod
    raw_fields: dict = Field(default_factory=dict)
    region: str = Field(default="", description="UF code, e.g. SP")
    declared_income: float = Field(default=0.0, ge=0)


def _analyze_and_persist(
    event: OriginationEvent,
    store: EventStore,
) -> None:
    """Background task: run analyzers, save result, write audit entry."""
    from trustline.llm.client import get_bedrock_client
    llm = get_bedrock_client()
    detector = InconsistencyDetector(llm)
    verifier = ConsentVerifier(llm)

    inconsistencies, confidence, reasoning = detector.analyze(event)
    consent_issues, consent_reasoning = verifier.verify(event)

    all_flags = inconsistencies + consent_issues
    flag_count = len(all_flags)

    # Risk scoring: flags → score heuristic for immediate feedback
    # Full correspondent re-score happens in the Airflow DAG (daily)
    if flag_count == 0:
        risk_score = 0.10
    elif flag_count == 1:
        risk_score = 0.40
    elif flag_count == 2:
        risk_score = 0.65
    else:
        risk_score = 0.85 + min(flag_count - 3, 3) * 0.05

    risk_level = RiskLevel.from_score(risk_score)

    full_reasoning = reasoning
    if consent_reasoning:
        full_reasoning += f" | Consentimento: {consent_reasoning}"

    result = AnalysisResult(
        event_id=event.event_id,
        correspondent_id=event.correspondent_id,
        risk_level=risk_level,
        risk_score=risk_score,
        inconsistencies=inconsistencies,
        consent_issues=consent_issues,
        confidence=confidence,
        llm_reasoning=full_reasoning,
        analyzer_version="1.0.0",
        analyzed_at=datetime.now(UTC),
    )
    store.save_analysis(result)

    decision = "approved" if risk_level == RiskLevel.LOW else (
        "flagged" if risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH) else "blocked"
    )
    audit = AuditEntry(
        entry_id=str(uuid.uuid4()),
        event_id=event.event_id,
        correspondent_id=event.correspondent_id,
        decision=decision,
        risk_level=risk_level,
        risk_score=risk_score,
        flags=all_flags,
        decided_at=datetime.now(UTC),
    )
    try:
        insert_audit_entry(audit)
    except Exception:
        pass  # audit trail write failure is non-blocking


@router.post("", status_code=202)
def ingest_event(
    payload: OriginationEventIn,
    background_tasks: BackgroundTasks,
    store: EventStore = Depends(get_event_store),
) -> dict:
    event = OriginationEvent(
        event_id=str(uuid.uuid4()),
        correspondent_id=payload.correspondent_id,
        channel=payload.channel,
        product_type=payload.product_type,
        customer_cpf_hash=hash_cpf(payload.customer_cpf),
        customer_age=payload.customer_age,
        loan_amount=payload.loan_amount,
        contract_date=datetime.fromisoformat(payload.contract_date),
        consent_method=payload.consent_method,
        raw_fields=payload.raw_fields,
        occurred_at=datetime.now(UTC),
        region=payload.region,
        declared_income=payload.declared_income,
    )

    inserted = store.append_event(event)
    if not inserted:
        raise HTTPException(status_code=409, detail="Duplicate event")

    background_tasks.add_task(_analyze_and_persist, event, store)

    return {
        "event_id": event.event_id,
        "status": "accepted",
        "message": "Evento recebido. Análise em andamento.",
    }


@router.get("/{event_id}")
def get_event(
    event_id: str,
    store: EventStore = Depends(get_event_store),
) -> dict:
    event = store.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    analysis = store.get_analysis(event_id)
    return {"event": event, "analysis": analysis}
