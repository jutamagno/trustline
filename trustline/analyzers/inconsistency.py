from __future__ import annotations

import logging
from datetime import UTC, datetime

from trustline.config import get_settings
from trustline.llm.client import BedrockClient, get_bedrock_client
from trustline.llm.prompts import inconsistency_detection_prompt
from trustline.models import OriginationEvent

logger = logging.getLogger(__name__)

# Hard rules applied before LLM — deterministic, cheap, fast
_MAX_LOAN_TO_INCOME_RATIO = 60.0    # max 60x monthly income (conservative)
_MIN_AGE = 18
_MAX_AGE = 90
_MIN_INCOME = 100.0                 # R$/month


def _hard_rules(event: OriginationEvent) -> list[str]:
    """Deterministic checks that don't need LLM."""
    issues = []

    if not (_MIN_AGE <= event.customer_age <= _MAX_AGE):
        issues.append(f"Idade inválida: {event.customer_age} anos")

    if event.declared_income < _MIN_INCOME:
        issues.append(f"Renda declarada implausível: R$ {event.declared_income:.2f}/mês")

    if event.declared_income > 0:
        ratio = event.loan_amount / event.declared_income
        if ratio > _MAX_LOAN_TO_INCOME_RATIO:
            issues.append(
                f"Valor do empréstimo ({event.loan_amount:.0f}) excede "
                f"{_MAX_LOAN_TO_INCOME_RATIO}x a renda mensal ({event.declared_income:.0f})"
            )

    return issues


class InconsistencyDetector:
    """
    Detects data inconsistencies in origination proposals.
    Applies hard rules first (no LLM cost), then LLM for nuanced analysis.
    """

    def __init__(self, llm: BedrockClient | None = None) -> None:
        self._llm = llm or get_bedrock_client()
        self._settings = get_settings()

    def analyze(self, event: OriginationEvent) -> tuple[list[str], float, str]:
        """
        Returns (inconsistencies, confidence, reasoning).
        Hard rules run always; LLM only if no critical hard-rule failures.
        """
        hard_issues = _hard_rules(event)

        # Skip LLM if basic data is already clearly invalid
        if len(hard_issues) >= 3:
            return hard_issues, 1.0, "Hard rules triggered — dados claramente inválidos."

        prompt = inconsistency_detection_prompt(
            raw_fields=event.raw_fields,
            customer_age=event.customer_age,
            declared_income=event.declared_income,
            product_type=event.product_type.value,
            channel=event.channel.value,
            loan_amount=event.loan_amount,
            region=event.region,
        )

        try:
            result = self._llm.invoke_json(prompt)
            llm_issues: list[str] = result.get("inconsistencies", [])
            confidence: float = float(result.get("confidence", 0.5))
            reasoning: str = result.get("reasoning", "")

            all_issues = hard_issues + [i for i in llm_issues if i not in hard_issues]

            logger.info(
                "inconsistency_analysis",
                extra={
                    "event_id": event.event_id,
                    "hard_issues": len(hard_issues),
                    "llm_issues": len(llm_issues),
                    "confidence": confidence,
                },
            )
            return all_issues, confidence, reasoning

        except Exception as exc:
            logger.error("inconsistency_llm_error",
                         extra={"event_id": event.event_id, "error": str(exc)})
            return hard_issues, 0.5, f"LLM indisponível. Hard rules: {hard_issues}"
