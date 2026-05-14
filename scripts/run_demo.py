#!/usr/bin/env python3
"""
End-to-end demo: ingest an event → analyze → show risk score → generate compliance report.
"""
from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from trustline.analyzers.consent_verifier import ConsentVerifier
from trustline.analyzers.inconsistency import InconsistencyDetector
from trustline.analyzers.risk_scorer import CorrespondentRiskScorer
from trustline.db.mongo import EventStore
from trustline.llm.client import BedrockClient
from trustline.llm.prompts import bcb538_narrative_prompt
from trustline.models import (
    Channel, ConsentMethod, OriginationEvent, ProductType, RiskLevel, hash_cpf,
)


def _section(title: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print('═' * 60)


def main() -> None:
    _section("TRUSTLINE — Demo End-to-End")
    print("Auditoria inteligente de dados de originação de crédito")
    print("Contexto: Banco BMG | BCB 538/2025 | LGPD")

    # 1. Ingest a suspicious event
    _section("1. Evento suspeito: idoso 74 anos, correspondente, áudio, madrugada")
    event = OriginationEvent(
        event_id=str(uuid.uuid4()),
        correspondent_id="CORR-DEMO-001",
        channel=Channel.CORRESPONDENT,
        product_type=ProductType.CONSIGNADO_INSS,
        customer_cpf_hash=hash_cpf("12345678900"),
        customer_age=74,
        loan_amount=8500.0,
        contract_date=datetime(2025, 3, 15, 2, 30, tzinfo=UTC),
        consent_method=ConsentMethod.AUDIO,
        raw_fields={"prazo_meses": 72, "taxa_juros": 1.8},
        occurred_at=datetime(2025, 3, 15, 2, 30, tzinfo=UTC),
        region="SP",
        declared_income=1500.0,
    )

    store = EventStore()
    store.append_event(event)
    print(f"Event ingested: {event.event_id}")
    print(f"  Correspondente: {event.correspondent_id}")
    print(f"  Canal: {event.channel.value} | Produto: {event.product_type.value}")
    print(f"  Horário: {event.occurred_at.strftime('%H:%M')} | Idade: {event.customer_age} anos")
    print(f"  Consentimento: {event.consent_method.value}")

    # 2. Run analyzers
    _section("2. Análise LLM")
    try:
        llm = BedrockClient()
        detector = InconsistencyDetector(llm)
        verifier = ConsentVerifier(llm)

        inconsistencies, confidence, reasoning = detector.analyze(event)
        consent_issues, consent_reasoning = verifier.verify(event)

        print(f"\n  Inconsistências ({len(inconsistencies)}):")
        for i in inconsistencies:
            print(f"    ⚠  {i}")

        print(f"\n  Problemas de consentimento ({len(consent_issues)}):")
        for i in consent_issues:
            print(f"    ⚠  {i}")

        print(f"\n  Raciocínio: {reasoning or 'N/A'}")
        print(f"  Confiança: {confidence:.0%}")

    except Exception as exc:
        print(f"  [LLM indisponível — modo demo sem Bedrock] {exc}")
        inconsistencies = ["Áudio como consentimento para idoso via correspondente (BCB 538)"]
        consent_issues = ["Método audio inválido para canal correspondent + consignado_inss"]

    # 3. Risk scoring
    _section("3. Score de risco do correspondente")
    events_history = store.get_correspondent_events("CORR-DEMO-001", days=30)
    results_history = store.get_correspondent_results("CORR-DEMO-001", days=30)

    try:
        scorer = CorrespondentRiskScorer(llm)
        score = scorer.score("CORR-DEMO-001", events_history, results_history)
        print(f"  Score: {score.score:.2f} | Risk level: {score.risk_level.value.upper()}")
        print(f"  Sinais: {score.signals}")
        store.save_risk_score(score)
    except Exception as exc:
        print(f"  [Scorer sem LLM] Heurística aplicada: {exc}")

    # 4. Compliance snapshot
    _section("4. Snapshot BCB 538/2025")
    stats = store.aggregate_stats(days=30)
    print(f"  Total eventos (30d): {stats['total_events']}")
    print(f"  Flagrados: {stats['flagged']} ({stats['detection_rate']:.1%})")
    print(f"  Bloqueados: {stats['blocked']}")
    print(f"  Distribuição de risco: {stats['risk_distribution']}")

    _section("5. Conclusão")
    all_flags = inconsistencies + consent_issues
    if all_flags:
        print(f"  RESULTADO: EVENTO FLAGRADO ({len(all_flags)} problemas detectados)")
        print("  Ação recomendada: revisar manualmente antes de originar o crédito")
    else:
        print("  RESULTADO: Evento aprovado para processamento")
    print("\n  Trustline — Auditoria em tempo real para crédito responsável")


if __name__ == "__main__":
    main()
