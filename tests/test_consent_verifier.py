import pytest
from conftest import make_event

from trustline.analyzers.consent_verifier import ConsentVerifier, _hard_consent_rules
from trustline.models import Channel, ConsentMethod, ProductType


class TestHardConsentRules:
    def test_video_ok_for_correspondent_inss(self):
        event = make_event(
            channel=Channel.CORRESPONDENT,
            product_type=ProductType.CONSIGNADO_INSS,
            consent_method=ConsentMethod.VIDEO,
        )
        assert _hard_consent_rules(event) == []

    def test_audio_fails_for_correspondent_inss(self):
        event = make_event(
            channel=Channel.CORRESPONDENT,
            product_type=ProductType.CONSIGNADO_INSS,
            consent_method=ConsentMethod.AUDIO,
        )
        issues = _hard_consent_rules(event)
        assert len(issues) == 1
        assert "videochamada" in issues[0].lower()

    def test_written_fails_for_digital_channel(self):
        event = make_event(
            channel=Channel.APP,
            product_type=ProductType.CONSIGNADO_PRIVADO,
            consent_method=ConsentMethod.WRITTEN,
        )
        issues = _hard_consent_rules(event)
        assert any("física" in i.lower() or "written" in i.lower() for i in issues)

    def test_audio_fails_for_elderly_70plus(self):
        event = make_event(
            channel=Channel.CORRESPONDENT,
            product_type=ProductType.CONSIGNADO_INSS,
            consent_method=ConsentMethod.AUDIO,
            customer_age=72,
        )
        issues = _hard_consent_rules(event)
        assert any("idoso" in i.lower() or "estatuto" in i.lower() for i in issues)

    def test_biometric_ok_for_correspondent_cartao(self):
        event = make_event(
            channel=Channel.CORRESPONDENT,
            product_type=ProductType.CARTAO_CONSIGNADO,
            consent_method=ConsentMethod.BIOMETRIC,
            customer_age=68,
        )
        assert _hard_consent_rules(event) == []

    def test_digital_signature_ok_for_api(self):
        event = make_event(
            channel=Channel.API,
            product_type=ProductType.CONSIGNADO_PRIVADO,
            consent_method=ConsentMethod.DIGITAL_SIGNATURE,
            customer_age=40,
        )
        assert _hard_consent_rules(event) == []


class TestConsentVerifier:
    def test_clear_violation_no_llm_called(self, mock_bedrock):
        verifier = ConsentVerifier(llm=mock_bedrock)
        event = make_event(
            channel=Channel.CORRESPONDENT,
            product_type=ProductType.CONSIGNADO_INSS,
            consent_method=ConsentMethod.AUDIO,
        )
        issues, _ = verifier.verify(event)
        assert issues
        mock_bedrock.invoke_json.assert_not_called()

    def test_borderline_calls_llm(self, mock_bedrock):
        mock_bedrock.invoke_json.return_value = {
            "adequate": True, "issues": [], "reasoning": "OK para este caso."
        }
        verifier = ConsentVerifier(llm=mock_bedrock)
        event = make_event(
            channel=Channel.APP,
            product_type=ProductType.CONSIGNADO_PRIVADO,
            consent_method=ConsentMethod.DIGITAL_SIGNATURE,
            customer_age=40,
        )
        issues, _ = verifier.verify(event)
        assert issues == []
        mock_bedrock.invoke_json.assert_called_once()

    def test_llm_flags_borderline_issue(self, mock_bedrock):
        mock_bedrock.invoke_json.return_value = {
            "adequate": False,
            "issues": ["Borderline: produto de alto risco sem vídeo"],
            "reasoning": "Recomendado vídeo para maior segurança.",
        }
        verifier = ConsentVerifier(llm=mock_bedrock)
        event = make_event(
            channel=Channel.BRANCH,
            product_type=ProductType.CONSIGNADO_PRIVADO,
            consent_method=ConsentMethod.DIGITAL_SIGNATURE,
            customer_age=55,
        )
        issues, _ = verifier.verify(event)
        assert "Borderline: produto de alto risco sem vídeo" in issues
