from __future__ import annotations

import logging

from trustline.llm.client import BedrockClient, get_bedrock_client
from trustline.llm.prompts import consent_borderline_prompt
from trustline.models import Channel, ConsentMethod, OriginationEvent, ProductType

logger = logging.getLogger(__name__)

# BCB 538/2025 + INSS agreement: required consent methods per channel/product
# Source: Termo de Compromisso BMG-INSS (2025) + Resolução BCB 538
_REQUIRED_VIDEO = {
    (Channel.CORRESPONDENT, ProductType.CONSIGNADO_INSS),
    (Channel.CORRESPONDENT, ProductType.CARTAO_CONSIGNADO),
    (Channel.BRANCH, ProductType.CONSIGNADO_INSS),
    (Channel.BRANCH, ProductType.CARTAO_CONSIGNADO),
}

_DIGITAL_ACCEPTABLE = {
    Channel.APP,
    Channel.API,
}

_ELDERLY_AGE_THRESHOLD = 70  # Estatuto do Idoso


def _hard_consent_rules(event: OriginationEvent) -> list[str]:
    """Deterministic consent validation based on BCB 538 + INSS agreement."""
    issues = []
    key = (event.channel, event.product_type)

    if key in _REQUIRED_VIDEO:
        if event.consent_method not in (ConsentMethod.VIDEO, ConsentMethod.BIOMETRIC):
            issues.append(
                f"Canal {event.channel.value} + produto {event.product_type.value} "
                f"requer videochamada ou biometria. Registrado: {event.consent_method.value}"
            )

    if event.channel in _DIGITAL_ACCEPTABLE:
        if event.consent_method == ConsentMethod.WRITTEN:
            issues.append(
                f"Assinatura física (written) incompatível com canal digital {event.channel.value}"
            )

    # Estatuto do Idoso: extra verification for 70+
    if event.customer_age >= _ELDERLY_AGE_THRESHOLD:
        if event.consent_method == ConsentMethod.AUDIO:
            issues.append(
                f"Cliente com {event.customer_age} anos: áudio sozinho é insuficiente "
                f"(Estatuto do Idoso — requer vídeo ou biometria)"
            )

    return issues


class ConsentVerifier:
    """
    Validates consent chain for origination events.
    Hard rules (BCB 538 / INSS agreement) run first.
    LLM handles borderline cases only.
    """

    def __init__(self, llm: BedrockClient | None = None) -> None:
        self._llm = llm or get_bedrock_client()

    def verify(self, event: OriginationEvent) -> tuple[list[str], str]:
        """Returns (issues, reasoning)."""
        hard_issues = _hard_consent_rules(event)

        # Clear violation: no need to call LLM
        if hard_issues:
            logger.info(
                "consent_hard_rule_violation",
                extra={"event_id": event.event_id, "issues": hard_issues},
            )
            return hard_issues, "Violação de regra determinística (BCB 538 / Acordo INSS)."

        # Borderline: LLM verifies edge cases
        prompt = consent_borderline_prompt(
            consent_method=event.consent_method.value,
            channel=event.channel.value,
            product_type=event.product_type.value,
            customer_age=event.customer_age,
        )

        try:
            result = self._llm.invoke_json(prompt)
            adequate: bool = result.get("adequate", True)
            llm_issues: list[str] = result.get("issues", [])
            reasoning: str = result.get("reasoning", "")

            if not adequate and llm_issues:
                logger.info(
                    "consent_llm_borderline",
                    extra={"event_id": event.event_id, "issues": llm_issues},
                )
                return llm_issues, reasoning

            return [], reasoning

        except Exception as exc:
            logger.error("consent_llm_error",
                         extra={"event_id": event.event_id, "error": str(exc)})
            return [], "LLM indisponível — apenas hard rules aplicadas."
