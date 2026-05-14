#!/usr/bin/env python3
"""Standalone LLM eval runner — CI-friendly, no infra required."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from trustline.llm.evaluator import LLMEvaluator
from trustline.models import EvalCase, OriginationEvent, RiskLevel


def load_cases(path: Path) -> list[EvalCase]:
    data = json.loads(path.read_text())
    cases = []
    for item in data:
        event_data = item["event"]
        # Validate age before creating event
        age = event_data["customer_age"]
        if age < 18:
            age = 18  # normalize for eval (the point is the data has age 17)
        from datetime import datetime
        from trustline.models import Channel, ConsentMethod, ProductType
        event = OriginationEvent(
            event_id=event_data["event_id"],
            correspondent_id=event_data["correspondent_id"],
            channel=Channel(event_data["channel"]),
            product_type=ProductType(event_data["product_type"]),
            customer_cpf_hash=event_data["customer_cpf_hash"],
            customer_age=event_data["customer_age"],
            loan_amount=event_data["loan_amount"],
            contract_date=datetime.fromisoformat(event_data["contract_date"]),
            consent_method=ConsentMethod(event_data["consent_method"]),
            raw_fields=event_data.get("raw_fields", {}),
            occurred_at=datetime.fromisoformat(event_data["occurred_at"]),
            region=event_data.get("region", ""),
            declared_income=event_data.get("declared_income", 0.0),
        )
        cases.append(EvalCase(
            case_id=item["case_id"],
            description=item["description"],
            event=event,
            expected_risk=RiskLevel(item["expected_risk"]),
            expected_flags=item.get("expected_flags", []),
        ))
    return cases


def main() -> int:
    dataset_path = Path(__file__).parent / "golden_dataset.json"
    cases = load_cases(dataset_path)
    print(f"Loaded {len(cases)} eval cases")

    evaluator = LLMEvaluator()
    metrics = evaluator.run_suite(cases, consistency_runs=1)  # 1 run in CI to save cost

    print("\n=== EVAL RESULTS ===")
    print(f"Total cases:          {metrics.total_cases}")
    print(f"Fraud cases:          {metrics.fraud_cases}")
    print(f"False negative rate:  {metrics.false_negative_rate:.1%}  (target: < 10%)")
    print(f"False positive rate:  {metrics.false_positive_rate:.1%}")
    print(f"Mean latency:         {metrics.mean_latency_ms:.0f} ms")
    print(f"PII leakage rate:     {metrics.pii_leakage_rate:.1%}  (target: 0%)")
    print(f"Consistency score:    {metrics.consistency_score:.1%}")
    print(f"Bedrock cost:         ${metrics.total_cost_usd:.4f}")

    # CI exit code: fail if critical thresholds exceeded
    failed = False
    if metrics.false_negative_rate > 0.10:
        print(f"\nFAIL: false_negative_rate {metrics.false_negative_rate:.1%} > 10% threshold")
        failed = True
    if metrics.pii_leakage_rate > 0.0:
        print(f"\nFAIL: PII leakage detected in {metrics.pii_leakage_rate:.1%} of responses")
        failed = True

    if not failed:
        print("\nPASS: All thresholds met.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
