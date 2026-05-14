"""DAG: Correspondent Risk Scoring — runs daily at 01:00."""
from __future__ import annotations

import sys
from pathlib import Path

from airflow.decorators import dag, task
from airflow.utils.dates import days_ago

sys.path.insert(0, str(Path(__file__).parent.parent))


@dag(
    dag_id="trustline_risk_scoring",
    schedule_interval="0 1 * * *",
    start_date=days_ago(1),
    catchup=False,
    tags=["trustline", "risk", "correspondents"],
    doc_md="""
    Recalculates risk scores for all active correspondents (any with events
    in the last 30 days). Uses CorrespondentRiskScorer: temporal signal
    analysis + LLM reasoning. Saves to MongoDB and PostgreSQL.
    Alerts via Kafka when a correspondent moves from LOW → HIGH/CRITICAL.
    """,
)
def trustline_risk_scoring():

    @task()
    def get_active_correspondents() -> list[str]:
        from trustline.db.mongo import EventStore
        store = EventStore()
        ids = store.active_correspondent_ids(days=30)
        print(f"Active correspondents: {len(ids)}")
        return ids

    @task()
    def score_correspondent(correspondent_id: str) -> dict:
        from trustline.analyzers.risk_scorer import CorrespondentRiskScorer
        from trustline.db.mongo import EventStore

        store = EventStore()
        events = store.get_correspondent_events(correspondent_id, days=30)
        results = store.get_correspondent_results(correspondent_id, days=30)

        scorer = CorrespondentRiskScorer()
        score = scorer.score(correspondent_id, events, results)

        # Persist
        store.save_risk_score(score)

        return {
            "correspondent_id": score.correspondent_id,
            "score": score.score,
            "risk_level": score.risk_level.value,
            "operations_30d": score.operations_30d,
        }

    @task()
    def detect_escalations(scores: list[dict]) -> None:
        """Alert when any correspondent hits HIGH or CRITICAL."""
        from trustline.config import get_settings
        from trustline.kafka.producer import publish_alert

        high_risk = [s for s in scores if s["risk_level"] in ("high", "critical")]
        if high_risk:
            try:
                publish_alert(
                    get_settings().eval_alerts_topic,
                    {
                        "alert_type": "correspondent_high_risk",
                        "correspondents": high_risk,
                    },
                )
                print(f"Escalation alerts sent for {len(high_risk)} correspondents")
            except Exception as exc:
                print(f"Alert failed (non-blocking): {exc}")

    @task()
    def publish_metrics(scores: list[dict]) -> None:
        from trustline import metrics as m
        for s in scores:
            m.set_gauge(
                "correspondent_risk_score",
                s["score"],
                correspondent_id=s["correspondent_id"],
            )
        by_level = {}
        for s in scores:
            by_level[s["risk_level"]] = by_level.get(s["risk_level"], 0) + 1
        for level, count in by_level.items():
            m.set_gauge("correspondent_risk_distribution", count, risk_level=level)
        print(f"Metrics published for {len(scores)} correspondents: {by_level}")

    correspondent_ids = get_active_correspondents()
    scores = score_correspondent.expand(correspondent_id=correspondent_ids)
    detect_escalations(scores)
    publish_metrics(scores)


trustline_risk_scoring()
