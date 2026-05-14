from datetime import UTC, datetime

import pytest

from trustline.analyzers.risk_scorer import (
    CorrespondentRiskScorer,
    _compute_signals,
    _heuristic_score,
)
from trustline.models import RiskLevel


def _make_event(hour: int = 10, region: str = "SP", flagged: bool = False) -> dict:
    return {
        "correspondent_id": "CORR-001",
        "channel": "correspondent",
        "product_type": "consignado_inss",
        "occurred_at": f"2025-06-01T{hour:02d}:00:00",
        "region": region,
    }


def _make_result(risk_level: str = "low") -> dict:
    return {"risk_level": risk_level, "analyzed_at": "2025-06-01T14:00:00"}


class TestComputeSignals:
    def test_empty_events_returns_no_data(self):
        signals = _compute_signals([], [])
        assert signals.get("no_data") is True

    def test_night_ops_detected(self):
        events = [_make_event(hour=h) for h in [2, 3, 4, 10, 14]]
        signals = _compute_signals(events, [])
        assert signals["night_ops_ratio"] == pytest.approx(0.6, abs=0.01)
        assert signals["night_ops_anomaly"] is True

    def test_normal_ops_no_night_anomaly(self):
        events = [_make_event(hour=h) for h in [9, 10, 11, 14, 15]]
        signals = _compute_signals(events, [])
        assert signals["night_ops_anomaly"] is False

    def test_flag_rate_computed(self):
        events = [_make_event() for _ in range(10)]
        results = [_make_result("high")] * 3 + [_make_result("low")] * 7
        signals = _compute_signals(events, results)
        assert signals["flag_rate"] == pytest.approx(0.3, abs=0.01)
        assert signals["high_flag_rate"] is True

    def test_geographic_concentration(self):
        events = [_make_event(region="SP")] * 9 + [_make_event(region="RJ")]
        signals = _compute_signals(events, [])
        assert signals["top_region_ratio"] == pytest.approx(0.9, abs=0.01)


class TestHeuristicScore:
    def test_clean_correspondent_low_score(self):
        signals = {
            "flag_rate": 0.02,
            "night_ops_anomaly": False,
            "velocity_anomaly": False,
            "top_region_ratio": 0.5,
            "top_product_ratio": 0.6,
        }
        assert _heuristic_score(signals) < 0.25

    def test_fraudulent_pattern_high_score(self):
        signals = {
            "flag_rate": 0.35,
            "night_ops_anomaly": True,
            "velocity_anomaly": True,
            "top_region_ratio": 0.95,
            "top_product_ratio": 0.95,
        }
        assert _heuristic_score(signals) >= 0.75


class TestCorrespondentRiskScorer:
    def test_no_events_returns_low_risk(self, mock_bedrock):
        scorer = CorrespondentRiskScorer(llm=mock_bedrock)
        result = scorer.score("CORR-EMPTY", [], [])
        assert result.risk_level == RiskLevel.LOW
        assert result.score < 0.25
        mock_bedrock.invoke_json.assert_not_called()

    def test_high_flag_rate_triggers_llm(self, mock_bedrock):
        mock_bedrock.invoke_json.return_value = {
            "risk_score": 0.8,
            "risk_level": "high",
            "reasoning": "Alta taxa de operações flagradas.",
        }
        events = [_make_event() for _ in range(20)]
        results = [_make_result("high")] * 5 + [_make_result("low")] * 15
        scorer = CorrespondentRiskScorer(llm=mock_bedrock)
        result = scorer.score("CORR-BAD", events, results)
        mock_bedrock.invoke_json.assert_called_once()
        assert result.score > 0.25

    def test_risk_level_from_score(self, mock_bedrock):
        mock_bedrock.invoke_json.return_value = {"risk_score": 0.9, "reasoning": ""}
        events = [_make_event(hour=3) for _ in range(20)]
        results = [_make_result("critical")] * 10 + [_make_result("low")] * 10
        scorer = CorrespondentRiskScorer(llm=mock_bedrock)
        result = scorer.score("CORR-CRITICAL", events, results)
        assert result.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
