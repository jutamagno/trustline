from __future__ import annotations

import re
import time
import logging
from datetime import UTC, datetime

from trustline.analyzers.inconsistency import InconsistencyDetector
from trustline.analyzers.consent_verifier import ConsentVerifier
from trustline.llm.client import BedrockClient, get_bedrock_client
from trustline.models import EvalCase, EvalMetrics, RiskLevel

logger = logging.getLogger(__name__)

# PII patterns we must never see in LLM output
_PII_PATTERNS = [
    re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"),  # CPF
    re.compile(r"\d{11}"),                           # CPF sem formatação
]


def _has_pii_leakage(text: str) -> bool:
    return any(p.search(text) for p in _PII_PATTERNS)


class LLMEvaluator:
    """
    Evaluates LLM-powered analyzers against a golden dataset.

    Metrics:
    - false_negative_rate: fraud not detected / total fraud cases — CRITICAL for banking
    - false_positive_rate: legitimate flagged / total legitimate cases
    - mean_latency_ms: average analysis latency
    - total_cost_usd: Bedrock token cost for the eval run
    - pii_leakage_rate: % of LLM responses containing PII patterns
    - consistency_score: same case → same decision across 3 runs
    """

    def __init__(self, llm: BedrockClient | None = None) -> None:
        self._llm = llm or get_bedrock_client()

    def run_suite(self, cases: list[EvalCase], consistency_runs: int = 3) -> EvalMetrics:
        logger.info("eval_suite_start", extra={"total_cases": len(cases)})
        self._llm.total_input_tokens = 0
        self._llm.total_output_tokens = 0

        detector = InconsistencyDetector(self._llm)
        verifier = ConsentVerifier(self._llm)

        latencies: list[float] = []
        pii_leakage_count = 0
        fraud_cases = 0
        false_negatives = 0
        false_positives = 0
        legitimate_cases = 0
        cases_detail: list[dict] = []
        consistency_scores: list[float] = []

        for case in cases:
            is_fraud = case.expected_risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
            if is_fraud:
                fraud_cases += 1
            else:
                legitimate_cases += 1

            # Primary run
            t0 = time.perf_counter()
            inconsistencies, confidence, reasoning = detector.analyze(case.event)
            consent_issues, consent_reason = verifier.verify(case.event)
            latency_ms = (time.perf_counter() - t0) * 1000
            latencies.append(latency_ms)

            all_text = reasoning + " " + consent_reason
            if _has_pii_leakage(all_text):
                pii_leakage_count += 1
                logger.warning("pii_leakage_detected",
                               extra={"case_id": case.case_id, "text_snippet": all_text[:100]})

            all_flags = inconsistencies + consent_issues
            flag_score = min(len(all_flags) * 0.3, 0.9)
            detected_level = RiskLevel.from_score(flag_score)

            detected_fraud = detected_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

            if is_fraud and not detected_fraud:
                false_negatives += 1
            if not is_fraud and detected_fraud:
                false_positives += 1

            # Consistency check: run same case N more times
            decisions = [detected_fraud]
            for _ in range(consistency_runs - 1):
                inc2, _, _ = detector.analyze(case.event)
                con2, _ = verifier.verify(case.event)
                flags2 = inc2 + con2
                score2 = min(len(flags2) * 0.3, 0.9)
                decisions.append(RiskLevel.from_score(score2) in (RiskLevel.HIGH, RiskLevel.CRITICAL))
            consistency = decisions.count(decisions[0]) / len(decisions)
            consistency_scores.append(consistency)

            cases_detail.append({
                "case_id": case.case_id,
                "description": case.description,
                "expected_risk": case.expected_risk.value,
                "detected_level": detected_level.value,
                "flags_found": all_flags,
                "expected_flags": case.expected_flags,
                "false_negative": is_fraud and not detected_fraud,
                "false_positive": not is_fraud and detected_fraud,
                "latency_ms": round(latency_ms, 1),
                "consistency": round(consistency, 2),
                "pii_leak": _has_pii_leakage(all_text),
            })

        n = len(cases)
        metrics = EvalMetrics(
            total_cases=n,
            fraud_cases=fraud_cases,
            false_negative_rate=round(false_negatives / fraud_cases, 4) if fraud_cases else 0.0,
            false_positive_rate=round(false_positives / legitimate_cases, 4) if legitimate_cases else 0.0,
            mean_latency_ms=round(sum(latencies) / n, 1) if n else 0.0,
            total_cost_usd=round(self._llm.estimated_cost_usd, 6),
            pii_leakage_rate=round(pii_leakage_count / n, 4) if n else 0.0,
            consistency_score=round(sum(consistency_scores) / len(consistency_scores), 4) if consistency_scores else 0.0,
            run_at=datetime.now(UTC),
            cases_detail=cases_detail,
        )

        logger.info(
            "eval_suite_complete",
            extra={
                "false_negative_rate": metrics.false_negative_rate,
                "false_positive_rate": metrics.false_positive_rate,
                "pii_leakage_rate": metrics.pii_leakage_rate,
                "consistency_score": metrics.consistency_score,
                "cost_usd": metrics.total_cost_usd,
            },
        )
        return metrics
