from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from conftest import make_event

from trustline.llm.evaluator import LLMEvaluator, _has_pii_leakage
from trustline.models import EvalCase, RiskLevel


def make_eval_case(case_id: str, expected_risk: RiskLevel, **event_kwargs) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        description=f"Test case {case_id}",
        event=make_event(**event_kwargs),
        expected_risk=expected_risk,
        expected_flags=[],
    )


class TestPIILeakageDetection:
    def test_detects_formatted_cpf(self):
        assert _has_pii_leakage("O cliente 123.456.789-00 solicitou crédito.")

    def test_detects_raw_cpf(self):
        assert _has_pii_leakage("CPF 12345678900 identificado na proposta.")

    def test_no_false_positive_on_normal_text(self):
        assert not _has_pii_leakage("Dados da proposta analisados. Sem inconsistências.")

    def test_no_false_positive_on_short_numbers(self):
        assert not _has_pii_leakage("Taxa de 1.75% ao mês, prazo de 36 meses.")


class TestLLMEvaluator:
    def _make_evaluator_with_mock(self, mock_bedrock) -> LLMEvaluator:
        return LLMEvaluator(llm=mock_bedrock)

    def test_no_fraud_cases_returns_zero_fnr(self, mock_bedrock):
        mock_bedrock.invoke_json.return_value = {
            "inconsistencies": [], "confidence": 0.9, "reasoning": "OK",
            "adequate": True, "issues": [],
        }
        evaluator = self._make_evaluator_with_mock(mock_bedrock)
        cases = [make_eval_case("legit_001", RiskLevel.LOW)]
        metrics = evaluator.run_suite(cases, consistency_runs=1)
        assert metrics.false_negative_rate == 0.0
        assert metrics.fraud_cases == 0

    def test_undetected_fraud_increments_fnr(self, mock_bedrock):
        """LLM returns no inconsistencies for a fraud case → false negative."""
        mock_bedrock.invoke_json.return_value = {
            "inconsistencies": [], "confidence": 0.5, "reasoning": "OK",
            "adequate": True, "issues": [],
        }
        evaluator = self._make_evaluator_with_mock(mock_bedrock)
        # A critical fraud case where LLM returns nothing
        cases = [make_eval_case("fraud_001", RiskLevel.CRITICAL)]
        metrics = evaluator.run_suite(cases, consistency_runs=1)
        assert metrics.false_negative_rate == 1.0  # 1 fraud, 0 detected

    def test_detected_fraud_zero_fnr(self, mock_bedrock):
        """LLM correctly flags fraud → no false negative."""
        mock_bedrock.invoke_json.return_value = {
            "inconsistencies": ["Renda implausível", "Prazo incomum"],
            "confidence": 0.9,
            "reasoning": "Dados suspeitos.",
            "adequate": True, "issues": [],
        }
        evaluator = self._make_evaluator_with_mock(mock_bedrock)
        cases = [make_eval_case(
            "fraud_002", RiskLevel.CRITICAL,
            loan_amount=50000.0, declared_income=500.0,
        )]
        metrics = evaluator.run_suite(cases, consistency_runs=1)
        assert metrics.false_negative_rate == 0.0

    def test_pii_leakage_detected(self, mock_bedrock):
        """LLM echoes CPF in reasoning → pii_leakage_rate > 0."""
        mock_bedrock.invoke_json.return_value = {
            "inconsistencies": [],
            "confidence": 0.5,
            "reasoning": "CPF 123.456.789-00 verificado.",  # PII leak!
            "adequate": True, "issues": [],
        }
        evaluator = self._make_evaluator_with_mock(mock_bedrock)
        cases = [make_eval_case("legit_001", RiskLevel.LOW)]
        metrics = evaluator.run_suite(cases, consistency_runs=1)
        assert metrics.pii_leakage_rate > 0.0

    def test_metrics_structure(self, mock_bedrock):
        mock_bedrock.invoke_json.return_value = {
            "inconsistencies": [], "confidence": 0.9, "reasoning": "OK",
            "adequate": True, "issues": [],
        }
        evaluator = self._make_evaluator_with_mock(mock_bedrock)
        cases = [
            make_eval_case("legit_001", RiskLevel.LOW),
            make_eval_case("legit_002", RiskLevel.LOW),
        ]
        metrics = evaluator.run_suite(cases, consistency_runs=1)
        assert metrics.total_cases == 2
        assert isinstance(metrics.mean_latency_ms, float)
        assert len(metrics.cases_detail) == 2
