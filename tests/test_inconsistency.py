import pytest
from conftest import make_event

from trustline.analyzers.inconsistency import InconsistencyDetector, _hard_rules
from trustline.models import Channel, ConsentMethod, ProductType


class TestHardRules:
    def test_no_issues_for_valid_event(self):
        event = make_event()
        assert _hard_rules(event) == []

    def test_invalid_age_too_young(self):
        event = make_event(customer_age=17)
        issues = _hard_rules(event)
        assert any("idade" in i.lower() for i in issues)

    def test_invalid_age_too_old(self):
        event = make_event(customer_age=91)
        issues = _hard_rules(event)
        assert any("idade" in i.lower() for i in issues)

    def test_income_too_low(self):
        event = make_event(declared_income=50.0)
        issues = _hard_rules(event)
        assert any("renda" in i.lower() for i in issues)

    def test_loan_to_income_ratio_exceeded(self):
        # R$50k loan with R$500/month income = 100x (limit: 60x)
        event = make_event(loan_amount=50000.0, declared_income=500.0)
        issues = _hard_rules(event)
        assert any("renda" in i.lower() or "60x" in i.lower() for i in issues)

    def test_acceptable_ratio(self):
        # R$10k loan with R$3k/month income = 3.3x — fine
        event = make_event(loan_amount=10000.0, declared_income=3000.0)
        assert _hard_rules(event) == []


class TestInconsistencyDetector:
    def test_no_issues_with_valid_event(self, mock_bedrock):
        mock_bedrock.invoke_json.return_value = {
            "inconsistencies": [], "confidence": 0.95, "reasoning": "Tudo OK."
        }
        detector = InconsistencyDetector(llm=mock_bedrock)
        event = make_event()
        issues, confidence, reasoning = detector.analyze(event)
        assert issues == []
        assert confidence == 0.95

    def test_llm_issues_appended(self, mock_bedrock):
        mock_bedrock.invoke_json.return_value = {
            "inconsistencies": ["Prazo de 84 meses incomum para INSS"],
            "confidence": 0.7,
            "reasoning": "Prazo atípico detectado.",
        }
        detector = InconsistencyDetector(llm=mock_bedrock)
        event = make_event()
        issues, confidence, _ = detector.analyze(event)
        assert "Prazo de 84 meses incomum para INSS" in issues
        assert confidence == 0.7

    def test_skips_llm_when_many_hard_rule_failures(self, mock_bedrock):
        """With 3+ hard rule failures, LLM should not be called."""
        detector = InconsistencyDetector(llm=mock_bedrock)
        # age=17, income=10, ratio=very high → 3 hard rule failures
        event = make_event(customer_age=17, declared_income=10.0, loan_amount=50000.0)
        issues, confidence, _ = detector.analyze(event)
        mock_bedrock.invoke_json.assert_not_called()
        assert confidence == 1.0

    def test_llm_error_falls_back_to_hard_rules(self, mock_bedrock):
        mock_bedrock.invoke_json.side_effect = Exception("Bedrock timeout")
        detector = InconsistencyDetector(llm=mock_bedrock)
        event = make_event(declared_income=50.0)  # triggers 1 hard rule
        issues, confidence, _ = detector.analyze(event)
        assert len(issues) >= 1
        assert confidence == 0.5
