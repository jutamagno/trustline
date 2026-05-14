from __future__ import annotations

from fastapi import APIRouter, HTTPException

from trustline.db.postgres import get_latest_compliance_report, get_latest_eval_run

router = APIRouter()


@router.get("/bcb538")
def get_bcb538_report() -> dict:
    report = get_latest_compliance_report("bcb538")
    if not report:
        raise HTTPException(status_code=404, detail="No BCB 538 report generated yet. Run the Airflow DAG.")
    return report


@router.get("/lgpd")
def get_lgpd_report() -> dict:
    report = get_latest_compliance_report("lgpd")
    if not report:
        raise HTTPException(status_code=404, detail="No LGPD audit generated yet. Run the Airflow DAG.")
    return report


@router.get("/eval/latest")
def get_latest_eval() -> dict:
    run = get_latest_eval_run()
    if not run:
        raise HTTPException(status_code=404, detail="No eval run found.")
    return run
