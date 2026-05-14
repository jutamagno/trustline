from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime

from trustline.config import get_settings
from trustline.llm.client import BedrockClient, get_bedrock_client
from trustline.llm.prompts import risk_scoring_prompt
from trustline.models import CorrespondentRiskScore, RiskLevel

logger = logging.getLogger(__name__)

_CANCELLATION_RATE_THRESHOLD = 0.20   # >20% → signal
_NIGHT_OPS_RATIO_THRESHOLD = 0.40     # >40% ops between 22h-6h → signal
_VELOCITY_THRESHOLD = 50              # >50 ops/day → signal
_HIGH_FLAG_RATE = 0.15                # >15% flagged → signal


def _compute_signals(events: list[dict], results: list[dict]) -> dict:
    """Compute temporal + behavioral signals from raw event documents."""
    n = len(events)
    if n == 0:
        return {"no_data": True}

    # Night operations ratio (22:00–06:00)
    night_count = 0
    for ev in events:
        try:
            h = datetime.fromisoformat(ev["occurred_at"]).hour
            if h >= 22 or h < 6:
                night_count += 1
        except Exception:
            pass
    night_ratio = night_count / n

    # Geographic concentration (single UF)
    regions = [ev.get("region", "") for ev in events if ev.get("region")]
    top_region_ratio = 0.0
    if regions:
        top_count = Counter(regions).most_common(1)[0][1]
        top_region_ratio = top_count / len(regions)

    # Flagged rate
    flagged = sum(
        1 for r in results
        if r.get("risk_level") in ("high", "critical")
    )
    flag_rate = flagged / n

    # Velocity (ops per day in window, assuming 30-day window)
    velocity_per_day = n / 30.0

    # Product mix: detect if >80% same product (concentration risk)
    products = [ev.get("product_type", "") for ev in events]
    top_product_ratio = 0.0
    if products:
        top_product_ratio = Counter(products).most_common(1)[0][1] / len(products)

    return {
        "total_operations": n,
        "flagged_operations": flagged,
        "flag_rate": round(flag_rate, 3),
        "night_ops_ratio": round(night_ratio, 3),
        "top_region_ratio": round(top_region_ratio, 3),
        "velocity_per_day": round(velocity_per_day, 1),
        "top_product_ratio": round(top_product_ratio, 3),
        "velocity_anomaly": velocity_per_day > _VELOCITY_THRESHOLD,
        "night_ops_anomaly": night_ratio > _NIGHT_OPS_RATIO_THRESHOLD,
        "high_flag_rate": flag_rate > _HIGH_FLAG_RATE,
    }


def _heuristic_score(signals: dict) -> float:
    """Fast heuristic score before LLM — 0.0 to 1.0."""
    score = 0.0
    score += min(signals.get("flag_rate", 0) * 2.0, 0.50)
    if signals.get("night_ops_anomaly"):
        score += 0.15
    if signals.get("velocity_anomaly"):
        score += 0.15
    if signals.get("top_region_ratio", 0) > 0.90:
        score += 0.10
    if signals.get("top_product_ratio", 0) > 0.90:
        score += 0.05
    return min(score, 1.0)


class CorrespondentRiskScorer:
    """
    Computes a risk score for a banking correspondent based on operational patterns.
    Heuristic score + LLM-generated reasoning for auditability.
    """

    def __init__(self, llm: BedrockClient | None = None) -> None:
        self._llm = llm or get_bedrock_client()
        self._settings = get_settings()

    def score(
        self,
        correspondent_id: str,
        events: list[dict],
        results: list[dict],
    ) -> CorrespondentRiskScore:
        signals = _compute_signals(events, results)
        heuristic = _heuristic_score(signals)

        flagged_30d = signals.get("flagged_operations", 0)
        ops_30d = signals.get("total_operations", 0)

        reasoning = ""
        final_score = heuristic

        # Only call LLM if there's enough data and something is notable
        if ops_30d >= 5 and (heuristic > 0.10 or flagged_30d > 0):
            prompt = risk_scoring_prompt(
                correspondent_id=correspondent_id,
                signals=signals,
                operations_30d=ops_30d,
                flagged_30d=flagged_30d,
            )
            try:
                result = self._llm.invoke_json(prompt)
                llm_score = float(result.get("risk_score", heuristic))
                reasoning = result.get("reasoning", "")
                # Blend: 60% heuristic (deterministic), 40% LLM (nuanced)
                final_score = 0.6 * heuristic + 0.4 * llm_score
            except Exception as exc:
                logger.error("risk_scorer_llm_error",
                             extra={"correspondent_id": correspondent_id, "error": str(exc)})
                reasoning = f"LLM indisponível — score baseado em heurística. Heurística: {heuristic:.2f}"

        final_score = round(min(max(final_score, 0.0), 1.0), 4)
        risk_level = RiskLevel.from_score(final_score)

        logger.info(
            "correspondent_scored",
            extra={
                "correspondent_id": correspondent_id,
                "score": final_score,
                "risk_level": risk_level.value,
                "ops_30d": ops_30d,
            },
        )

        return CorrespondentRiskScore(
            correspondent_id=correspondent_id,
            score=final_score,
            risk_level=risk_level,
            signals=signals,
            operations_30d=ops_30d,
            flagged_30d=flagged_30d,
            llm_reasoning=reasoning,
            computed_at=datetime.now(UTC),
        )
