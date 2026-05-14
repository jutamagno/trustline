"""DAG: LLM Eval Suite — runs daily at 02:00, publishes metrics, alerts on regression."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from airflow.decorators import dag, task
from airflow.utils.dates import days_ago

TRUSTLINE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(TRUSTLINE_ROOT))


@dag(
    dag_id="trustline_llm_eval",
    schedule_interval="0 2 * * *",
    start_date=days_ago(1),
    catchup=False,
    tags=["trustline", "llm", "eval"],
    doc_md="""
    Runs the LLM evaluation suite against the golden dataset (fraud scenarios
    based on public INSS/2025 cases). Saves metrics to PostgreSQL and alerts
    via Kafka if false_negative_rate exceeds 10% threshold.

    Addresses: BCB 538/2025 requirement for continuous monitoring of automated
    decision systems.
    """,
)
def trustline_llm_eval():

    @task()
    def load_golden_dataset() -> list[dict]:
        dataset_path = TRUSTLINE_ROOT / "eval" / "golden_dataset.json"
        return json.loads(dataset_path.read_text())

    @task()
    def run_eval_suite(cases_raw: list[dict]) -> dict:
        from datetime import datetime
        from trustline.llm.evaluator import LLMEvaluator
        from trustline.models import (
            Channel, ConsentMethod, EvalCase, OriginationEvent, ProductType, RiskLevel,
        )

        cases = []
        for item in cases_raw:
            ev = item["event"]
            event = OriginationEvent(
                event_id=ev["event_id"],
                correspondent_id=ev["correspondent_id"],
                channel=Channel(ev["channel"]),
                product_type=ProductType(ev["product_type"]),
                customer_cpf_hash=ev["customer_cpf_hash"],
                customer_age=ev["customer_age"],
                loan_amount=ev["loan_amount"],
                contract_date=datetime.fromisoformat(ev["contract_date"]),
                consent_method=ConsentMethod(ev["consent_method"]),
                raw_fields=ev.get("raw_fields", {}),
                occurred_at=datetime.fromisoformat(ev["occurred_at"]),
                region=ev.get("region", ""),
                declared_income=ev.get("declared_income", 0.0),
            )
            cases.append(EvalCase(
                case_id=item["case_id"],
                description=item["description"],
                event=event,
                expected_risk=RiskLevel(item["expected_risk"]),
                expected_flags=item.get("expected_flags", []),
            ))

        evaluator = LLMEvaluator()
        metrics = evaluator.run_suite(cases, consistency_runs=2)
        return {
            "total_cases": metrics.total_cases,
            "fraud_cases": metrics.fraud_cases,
            "false_negative_rate": metrics.false_negative_rate,
            "false_positive_rate": metrics.false_positive_rate,
            "mean_latency_ms": metrics.mean_latency_ms,
            "total_cost_usd": metrics.total_cost_usd,
            "pii_leakage_rate": metrics.pii_leakage_rate,
            "consistency_score": metrics.consistency_score,
            "run_at": metrics.run_at.isoformat(),
            "cases_detail": metrics.cases_detail,
        }

    @task()
    def save_metrics(metrics: dict) -> None:
        from datetime import datetime
        from trustline.db.postgres import ensure_schema, save_eval_run
        from trustline.models import EvalMetrics

        ensure_schema()
        m = EvalMetrics(
            total_cases=metrics["total_cases"],
            fraud_cases=metrics["fraud_cases"],
            false_negative_rate=metrics["false_negative_rate"],
            false_positive_rate=metrics["false_positive_rate"],
            mean_latency_ms=metrics["mean_latency_ms"],
            total_cost_usd=metrics["total_cost_usd"],
            pii_leakage_rate=metrics["pii_leakage_rate"],
            consistency_score=metrics["consistency_score"],
            run_at=datetime.fromisoformat(metrics["run_at"]),
            cases_detail=metrics.get("cases_detail", []),
        )
        run_id = save_eval_run(m)
        print(f"Eval run saved: {run_id}")

    @task()
    def alert_on_regression(metrics: dict) -> None:
        import os
        FNR_THRESHOLD = 0.10
        PII_THRESHOLD = 0.0

        alerts = []
        if metrics["false_negative_rate"] > FNR_THRESHOLD:
            alerts.append(
                f"FALSE_NEGATIVE_RATE {metrics['false_negative_rate']:.1%} > {FNR_THRESHOLD:.0%}"
            )
        if metrics["pii_leakage_rate"] > PII_THRESHOLD:
            alerts.append(
                f"PII_LEAKAGE {metrics['pii_leakage_rate']:.1%} > 0%"
            )

        if alerts:
            try:
                from trustline.kafka.producer import publish_alert
                from trustline.config import get_settings
                publish_alert(
                    get_settings().eval_alerts_topic,
                    {"alerts": alerts, "metrics": metrics},
                )
                print(f"ALERT published: {alerts}")
            except Exception as exc:
                print(f"Alert publish failed (non-blocking): {exc}")

    raw = load_golden_dataset()
    metrics = run_eval_suite(raw)
    save_metrics(metrics)
    alert_on_regression(metrics)


trustline_llm_eval()
