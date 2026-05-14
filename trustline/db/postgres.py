from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Generator

import psycopg2
import psycopg2.extras

from trustline.config import get_settings
from trustline.models import AuditEntry, EvalMetrics

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS audit_trail (
    entry_id        TEXT PRIMARY KEY,
    event_id        TEXT NOT NULL,
    correspondent_id TEXT NOT NULL,
    decision        TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    risk_score      FLOAT NOT NULL,
    flags           JSONB NOT NULL DEFAULT '[]',
    decided_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_correspondent ON audit_trail(correspondent_id);
CREATE INDEX IF NOT EXISTS idx_audit_decided_at    ON audit_trail(decided_at);
CREATE INDEX IF NOT EXISTS idx_audit_risk_level    ON audit_trail(risk_level);

CREATE TABLE IF NOT EXISTS llm_eval_runs (
    run_id          TEXT PRIMARY KEY,
    total_cases     INT,
    fraud_cases     INT,
    false_negative_rate FLOAT,
    false_positive_rate FLOAT,
    mean_latency_ms FLOAT,
    total_cost_usd  FLOAT,
    pii_leakage_rate FLOAT,
    consistency_score FLOAT,
    cases_detail    JSONB,
    run_at          TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS compliance_reports (
    report_id       TEXT PRIMARY KEY,
    report_type     TEXT NOT NULL,
    report_date     TEXT NOT NULL,
    content         JSONB NOT NULL,
    s3_path         TEXT,
    generated_at    TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_report_type_date ON compliance_reports(report_type, report_date);
"""


@lru_cache(maxsize=1)
def _get_conn_params() -> str:
    return get_settings().postgres_url


@contextmanager
def get_conn() -> Generator:
    conn = psycopg2.connect(_get_conn_params())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema() -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
    logger.info("postgres_schema_ready")


def insert_audit_entry(entry: AuditEntry) -> None:
    sql = """
        INSERT INTO audit_trail
            (entry_id, event_id, correspondent_id, decision, risk_level,
             risk_score, flags, decided_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (entry_id) DO NOTHING
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                entry.entry_id, entry.event_id, entry.correspondent_id,
                entry.decision, entry.risk_level.value, entry.risk_score,
                psycopg2.extras.Json(entry.flags),
                entry.decided_at,
            ))


def query_audit_trail(
    correspondent_id: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    risk_level: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conditions = []
    params: list[Any] = []
    if correspondent_id:
        conditions.append("correspondent_id = %s")
        params.append(correspondent_id)
    if from_dt:
        conditions.append("decided_at >= %s")
        params.append(from_dt)
    if to_dt:
        conditions.append("decided_at <= %s")
        params.append(to_dt)
    if risk_level:
        conditions.append("risk_level = %s")
        params.append(risk_level)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT entry_id, event_id, correspondent_id, decision, risk_level,
               risk_score, flags, decided_at
        FROM audit_trail {where}
        ORDER BY decided_at DESC
        LIMIT %s OFFSET %s
    """
    params += [limit, offset]

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def save_eval_run(metrics: EvalMetrics) -> str:
    run_id = str(uuid.uuid4())
    sql = """
        INSERT INTO llm_eval_runs
            (run_id, total_cases, fraud_cases, false_negative_rate, false_positive_rate,
             mean_latency_ms, total_cost_usd, pii_leakage_rate, consistency_score,
             cases_detail, run_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                run_id, metrics.total_cases, metrics.fraud_cases,
                metrics.false_negative_rate, metrics.false_positive_rate,
                metrics.mean_latency_ms, metrics.total_cost_usd,
                metrics.pii_leakage_rate, metrics.consistency_score,
                psycopg2.extras.Json(metrics.cases_detail),
                metrics.run_at,
            ))
    return run_id


def save_compliance_report(
    report_type: str,
    report_date: str,
    content: dict,
    s3_path: str = "",
) -> str:
    report_id = str(uuid.uuid4())
    sql = """
        INSERT INTO compliance_reports
            (report_id, report_type, report_date, content, s3_path, generated_at)
        VALUES (%s,%s,%s,%s,%s,%s)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                report_id, report_type, report_date,
                psycopg2.extras.Json(content), s3_path,
                datetime.now(UTC),
            ))
    return report_id


def get_latest_compliance_report(report_type: str) -> dict | None:
    sql = """
        SELECT report_id, report_type, report_date, content, s3_path, generated_at
        FROM compliance_reports
        WHERE report_type = %s
        ORDER BY report_date DESC, generated_at DESC
        LIMIT 1
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (report_type,))
            row = cur.fetchone()
            return dict(row) if row else None


def get_latest_eval_run() -> dict | None:
    sql = """
        SELECT run_id, total_cases, fraud_cases, false_negative_rate,
               false_positive_rate, mean_latency_ms, total_cost_usd,
               pii_leakage_rate, consistency_score, run_at
        FROM llm_eval_runs
        ORDER BY run_at DESC
        LIMIT 1
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            row = cur.fetchone()
            return dict(row) if row else None
