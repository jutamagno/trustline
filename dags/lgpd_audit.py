"""DAG: LGPD Consent Audit — runs weekly Monday 03:00."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from airflow.decorators import dag, task
from airflow.utils.dates import days_ago

sys.path.insert(0, str(Path(__file__).parent.parent))

# LGPD data retention: 5 years for credit operations (BACEN resolution)
_RETENTION_YEARS = 5


@dag(
    dag_id="trustline_lgpd_audit",
    schedule_interval="0 3 * * 1",  # Monday 03:00
    start_date=days_ago(1),
    catchup=False,
    tags=["trustline", "compliance", "lgpd"],
    doc_md="""
    Weekly LGPD compliance audit. Inventories all processed CPF hashes,
    verifies consent method and legal basis per product type, checks
    data retention deadlines, and generates the LGPD inventory report
    saved to PostgreSQL + S3.

    Basis: LGPD Art. 7 (legitimate interest, contract), BACEN Circular 3.947.
    """,
)
def trustline_lgpd_audit():

    @task()
    def build_consent_inventory() -> list[dict]:
        from trustline.db.mongo import EventStore
        store = EventStore()
        inventory = store.get_consent_inventory()
        print(f"Consent inventory: {len(inventory)} unique cpf+product combinations")
        return inventory

    @task()
    def validate_retention(inventory: list[dict]) -> dict:
        today = date.today()
        retention_deadline = today - timedelta(days=_RETENTION_YEARS * 365)
        violations = []
        valid = 0

        for item in inventory:
            last_seen_str = item.get("last_seen", "")
            try:
                last_seen = date.fromisoformat(last_seen_str[:10])
                if last_seen < retention_deadline:
                    violations.append({
                        "cpf_hash": item["cpf_hash"],
                        "product_type": item.get("product_type"),
                        "last_seen": last_seen_str,
                        "issue": "Retenção expirada — dado deve ser expurgado",
                    })
                else:
                    valid += 1
            except Exception:
                violations.append({
                    "cpf_hash": item.get("cpf_hash"),
                    "issue": "Data last_seen inválida",
                })

        return {
            "total": len(inventory),
            "valid": valid,
            "violations": violations,
            "violation_count": len(violations),
        }

    @task()
    def classify_legal_bases(inventory: list[dict]) -> dict:
        """Map product type → legal basis under LGPD Art. 7."""
        basis_map = {
            "consignado_inss": "execução de contrato (LGPD Art. 7, V) + obrigação legal (BACEN)",
            "consignado_privado": "execução de contrato (LGPD Art. 7, V)",
            "cartao_consignado": "execução de contrato (LGPD Art. 7, V)",
        }
        summary = {}
        for item in inventory:
            pt = item.get("product_type", "unknown")
            basis = basis_map.get(pt, "base legal não mapeada — revisar")
            summary[pt] = {"legal_basis": basis, "count": summary.get(pt, {}).get("count", 0) + 1}
        return summary

    @task()
    def save_lgpd_report(inventory: list[dict], retention: dict, legal_bases: dict) -> None:
        import json
        import boto3
        from trustline.config import get_settings
        from trustline.db.postgres import ensure_schema, save_compliance_report

        ensure_schema()
        report_date = str(date.today())
        report = {
            "report_type": "lgpd",
            "regulation": "Lei 13.709/2018 (LGPD) + BACEN Circular 3.947",
            "report_date": report_date,
            "summary": {
                "total_data_subjects": retention["total"],
                "valid_retention": retention["valid"],
                "retention_violations": retention["violation_count"],
            },
            "legal_bases": legal_bases,
            "retention_violations": retention["violations"][:50],  # cap for report size
            "data_sharing": {
                "third_parties": ["correspondentes bancários (finalidade: intermediação de crédito)"],
                "basis": "execução de contrato + consentimento específico",
                "safeguards": "dados pessoais não compartilhados além da finalidade declarada (BCB 538)",
            },
        }

        settings = get_settings()
        s3_path = f"compliance/lgpd/{report_date}/audit.json"
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
        except Exception as exc:
            print(f"S3 save failed (non-blocking): {exc}")
            s3_path = ""

        report_id = save_compliance_report(
            report_type="lgpd",
            report_date=report_date,
            content=report,
            s3_path=s3_path,
        )
        if retention["violation_count"] > 0:
            print(f"WARNING: {retention['violation_count']} LGPD retention violations found!")
        print(f"LGPD audit saved: {report_id}")

    inventory = build_consent_inventory()
    retention = validate_retention(inventory)
    bases = classify_legal_bases(inventory)
    save_lgpd_report(inventory, retention, bases)


trustline_lgpd_audit()
