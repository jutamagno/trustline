from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── FastAPI test client ───────────────────────────────────────────────────────
from fastapi.testclient import TestClient


@pytest.fixture
def mock_bedrock():
    """Bedrock client that returns canned JSON responses."""
    client = MagicMock()
    client.invoke.return_value = '{"inconsistencies": [], "confidence": 0.9, "reasoning": "Dados consistentes."}'
    client.invoke_json.return_value = {
        "inconsistencies": [],
        "confidence": 0.9,
        "reasoning": "Dados consistentes.",
    }
    client.estimated_cost_usd = 0.0
    client.total_input_tokens = 0
    client.total_output_tokens = 0
    return client


@pytest.fixture
def mock_event_store():
    store = MagicMock()
    store.append_event.return_value = True
    store.get_event.return_value = None
    store.get_analysis.return_value = None
    store.list_risk_scores.return_value = []
    store.get_risk_score.return_value = None
    store.get_correspondent_events.return_value = []
    store.get_correspondent_results.return_value = []
    store.active_correspondent_ids.return_value = []
    return store


@pytest.fixture
def mock_elastic():
    es = MagicMock()
    es.ping.return_value = True
    es.search_events.return_value = []
    es.natural_language_to_query.return_value = {"query": {"match_all": {}}}
    return es


@pytest.fixture
def app(mock_event_store, mock_elastic):
    from api.main import app as _app
    from api.deps import get_event_store, get_elastic_client
    _app.dependency_overrides[get_event_store] = lambda: mock_event_store
    _app.dependency_overrides[get_elastic_client] = lambda: mock_elastic
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    return TestClient(app)


# ── Sample events ─────────────────────────────────────────────────────────────
from datetime import UTC, datetime

from trustline.models import Channel, ConsentMethod, OriginationEvent, ProductType


def make_event(**overrides) -> OriginationEvent:
    defaults = dict(
        event_id="test-event-001",
        correspondent_id="CORR-001",
        channel=Channel.CORRESPONDENT,
        product_type=ProductType.CONSIGNADO_INSS,
        customer_cpf_hash="aabbcc",
        customer_age=65,
        loan_amount=5000.0,
        contract_date=datetime(2025, 6, 1, 14, 0, tzinfo=UTC),
        consent_method=ConsentMethod.VIDEO,
        raw_fields={"prazo_meses": 36, "taxa_juros": 1.7},
        occurred_at=datetime(2025, 6, 1, 14, 0, tzinfo=UTC),
        region="SP",
        declared_income=2500.0,
    )
    defaults.update(overrides)
    return OriginationEvent(**defaults)
