from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Channel(str, Enum):
    CORRESPONDENT = "correspondent"
    APP = "app"
    API = "api"
    BRANCH = "branch"


class ProductType(str, Enum):
    CONSIGNADO_INSS = "consignado_inss"
    CONSIGNADO_PRIVADO = "consignado_privado"
    CARTAO_CONSIGNADO = "cartao_consignado"


class ConsentMethod(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"
    DIGITAL_SIGNATURE = "digital_signature"
    BIOMETRIC = "biometric"
    WRITTEN = "written"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def score_threshold(self) -> float:
        return {"low": 0.25, "medium": 0.50, "high": 0.75, "critical": 1.0}[self.value]

    @staticmethod
    def from_score(score: float) -> "RiskLevel":
        if score < 0.25:
            return RiskLevel.LOW
        if score < 0.50:
            return RiskLevel.MEDIUM
        if score < 0.75:
            return RiskLevel.HIGH
        return RiskLevel.CRITICAL


def hash_cpf(cpf: str) -> str:
    """SHA-256 of CPF — CPF never stored in plaintext."""
    return hashlib.sha256(cpf.strip().encode()).hexdigest()


@dataclass
class OriginationEvent:
    event_id: str
    correspondent_id: str
    channel: Channel
    product_type: ProductType
    customer_cpf_hash: str          # always SHA-256, never raw CPF
    customer_age: int
    loan_amount: float
    contract_date: datetime
    consent_method: ConsentMethod
    raw_fields: dict[str, Any]      # proposal fields for inconsistency analysis
    occurred_at: datetime
    region: str = ""                # UF code, e.g. "SP"
    declared_income: float = 0.0

    def to_doc(self) -> dict:
        return {
            "event_id": self.event_id,
            "correspondent_id": self.correspondent_id,
            "channel": self.channel.value,
            "product_type": self.product_type.value,
            "customer_cpf_hash": self.customer_cpf_hash,
            "customer_age": self.customer_age,
            "loan_amount": self.loan_amount,
            "contract_date": self.contract_date.isoformat(),
            "consent_method": self.consent_method.value,
            "raw_fields": self.raw_fields,
            "occurred_at": self.occurred_at.isoformat(),
            "region": self.region,
            "declared_income": self.declared_income,
        }

    @classmethod
    def from_doc(cls, doc: dict) -> "OriginationEvent":
        return cls(
            event_id=doc["event_id"],
            correspondent_id=doc["correspondent_id"],
            channel=Channel(doc["channel"]),
            product_type=ProductType(doc["product_type"]),
            customer_cpf_hash=doc["customer_cpf_hash"],
            customer_age=doc["customer_age"],
            loan_amount=doc["loan_amount"],
            contract_date=datetime.fromisoformat(doc["contract_date"]),
            consent_method=ConsentMethod(doc["consent_method"]),
            raw_fields=doc.get("raw_fields", {}),
            occurred_at=datetime.fromisoformat(doc["occurred_at"]),
            region=doc.get("region", ""),
            declared_income=doc.get("declared_income", 0.0),
        )


@dataclass
class AnalysisResult:
    event_id: str
    correspondent_id: str
    risk_level: RiskLevel
    risk_score: float
    inconsistencies: list[str]
    consent_issues: list[str]
    confidence: float
    llm_reasoning: str              # auditable LLM trace
    analyzer_version: str
    analyzed_at: datetime

    def to_doc(self) -> dict:
        return {
            "event_id": self.event_id,
            "correspondent_id": self.correspondent_id,
            "risk_level": self.risk_level.value,
            "risk_score": self.risk_score,
            "inconsistencies": self.inconsistencies,
            "consent_issues": self.consent_issues,
            "confidence": self.confidence,
            "llm_reasoning": self.llm_reasoning,
            "analyzer_version": self.analyzer_version,
            "analyzed_at": self.analyzed_at.isoformat(),
        }


@dataclass
class CorrespondentRiskScore:
    correspondent_id: str
    score: float                    # 0.0–1.0
    risk_level: RiskLevel
    signals: dict[str, Any]         # {cancellation_rate, velocity_anomaly, ...}
    operations_30d: int
    flagged_30d: int
    llm_reasoning: str
    computed_at: datetime

    def to_doc(self) -> dict:
        return {
            "correspondent_id": self.correspondent_id,
            "score": self.score,
            "risk_level": self.risk_level.value,
            "signals": self.signals,
            "operations_30d": self.operations_30d,
            "flagged_30d": self.flagged_30d,
            "llm_reasoning": self.llm_reasoning,
            "computed_at": self.computed_at.isoformat(),
        }


@dataclass
class AuditEntry:
    entry_id: str
    event_id: str
    correspondent_id: str
    decision: str                   # "approved", "flagged", "blocked"
    risk_level: RiskLevel
    risk_score: float
    flags: list[str]
    decided_at: datetime


@dataclass
class ConsentRecord:
    cpf_hash: str
    product_type: ProductType
    channel: Channel
    consent_method: ConsentMethod
    correspondent_id: str
    consented_at: datetime
    retention_until: datetime       # LGPD: data retention deadline
    legal_basis: str                # e.g., "contrato", "interesse legítimo"


@dataclass
class ComplianceBCB538Report:
    report_date: str                # YYYY-MM-DD
    period_start: str
    period_end: str
    total_events: int
    total_flagged: int
    total_blocked: int
    detection_rate: float
    correspondent_risk_distribution: dict[str, int]  # {LOW: n, MEDIUM: n, ...}
    llm_eval_false_negative_rate: float
    narrative_md: str               # Markdown gerado por LLM
    generated_at: str


@dataclass
class EvalCase:
    case_id: str
    description: str
    event: OriginationEvent
    expected_risk: RiskLevel
    expected_flags: list[str]       # substrings esperadas nas inconsistências


@dataclass
class EvalMetrics:
    total_cases: int
    fraud_cases: int
    false_negative_rate: float      # fraude não detectada / total fraudes — crítico
    false_positive_rate: float
    mean_latency_ms: float
    total_cost_usd: float           # tokens Bedrock
    pii_leakage_rate: float         # % respostas com CPF/dados pessoais
    consistency_score: float        # mesmo caso → mesma decisão em 3 runs
    run_at: datetime
    cases_detail: list[dict] = field(default_factory=list)
