"""DAG: BCB 538/2025 Compliance Report — runs daily at 06:00."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from airflow.decorators import dag, task
from airflow.utils.dates import days_ago

sys.path.insert(0, str(Path(__file__).parent.parent))


@dag(
    dag_id="trustline_bcb538_report",
    schedule_interval="0 6 * * *",
    start_date=days_ago(1),
    catchup=False,
    tags=["trustline", "compliance", "bcb538"],
    doc_md="""
    Generates the daily BCB 538/2025 compliance report (Resolução BCB 538/2025
    — Cibersegurança e Proteção de Dados, compliance deadline: Dec 2026).

    Aggregates: event volume, detection rates, consent audit, correspondent
    risk distribution, and LLM eval metrics. Generates JSON + LLM-written
    Markdown narrative and saves to S3 + PostgreSQL.
    """,
)
def trustline_bcb538_report():

    @task()
    def collect_stats() -> dict:
        from trustline.db.mongo import EventStore
        from trustline.db.postgres import get_latest_eval_run

        store = EventStore()
        stats = store.aggregate_stats(days=1)  # last 24h
        eval_run = get_latest_eval_run()

        return {
            **stats,
            "llm_eval_false_negative_rate": eval_run["false_negative_rate"] if eval_run else None,
            "llm_eval_pii_leakage_rate": eval_run["pii_leakage_rate"] if eval_run else None,
            "report_date": str(date.today()),
        }

    @task()
    def generate_narrative(stats: dict) -> str:
        from trustline.llm.client import get_bedrock_client
        from trustline.llm.prompts import bcb538_narrative_prompt

        period = f"{stats['report_date']} (últimas 24h)"
        prompt = bcb538_narrative_prompt(stats=stats, period=period)
        llm = get_bedrock_client()
        return llm.invoke(prompt, max_tokens=600)

    @task()
    def build_and_save_report(stats: dict, narrative: str) -> str:
        import json
        import boto3
        from trustline.config import get_settings
        from trustline.db.postgres import ensure_schema, save_compliance_report

        ensure_schema()
        report = {
            "report_type": "bcb538",
            "regulation": "Resolução BCB 538/2025 — Cibersegurança e Proteção de Dados",
            "compliance_deadline": "2026-12-31",
            "report_date": stats["report_date"],
            "period": "últimas 24h",
            "metrics": {
                "total_events": stats.get("total_events", 0),
                "flagged_events": stats.get("flagged", 0),
                "blocked_events": stats.get("blocked", 0),
                "detection_rate": stats.get("detection_rate", 0.0),
                "correspondent_risk_distribution": stats.get("risk_distribution", {}),
                "llm_false_negative_rate": stats.get("llm_eval_false_negative_rate"),
                "llm_pii_leakage_rate": stats.get("llm_eval_pii_leakage_rate"),
            },
            "narrative_md": narrative,
        }

        settings = get_settings()
        s3_path = f"compliance/bcb538/{stats['report_date']}/report.json"

        try:
            s3 = boto3.client(
                "s3",
                region_name=settings.aws_region,
                **({"endpoint_url": settings.localstack_endpoint} if settings.use_localstack else {}),
            )
            s3.put_object(
                Bucket=settings.s3_compliance_bucket,
                Key=s3_path,
                Body=json.dumps(report, ensure_ascii=False, indent=2),
                ContentType="application/json",
            )
            print(f"Report saved to S3: s3://{settings.s3_compliance_bucket}/{s3_path}")
        except Exception as exc:
            print(f"S3 save failed (non-blocking): {exc}")
            s3_path = ""

        report_id = save_compliance_report(
            report_type="bcb538",
            report_date=stats["report_date"],
            content=report,
            s3_path=s3_path,
        )
        print(f"BCB 538 report saved: {report_id}")
        return report_id

    stats = collect_stats()
    narrative = generate_narrative(stats)
    build_and_save_report(stats, narrative)


trustline_bcb538_report()
