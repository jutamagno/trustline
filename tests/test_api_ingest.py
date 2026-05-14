from unittest.mock import patch


class TestIngestRoute:
    def test_ingest_accepted(self, client):
        resp = client.post("/events", json={
            "correspondent_id": "CORR-001",
            "channel": "correspondent",
            "product_type": "consignado_inss",
            "customer_cpf": "123.456.789-00",
            "customer_age": 65,
            "loan_amount": 5000.0,
            "contract_date": "2025-06-01T14:00:00",
            "consent_method": "video",
            "region": "SP",
            "declared_income": 2500.0,
        })
        assert resp.status_code == 202
        data = resp.json()
        assert "event_id" in data
        assert data["status"] == "accepted"

    def test_ingest_invalid_age(self, client):
        resp = client.post("/events", json={
            "correspondent_id": "CORR-001",
            "channel": "app",
            "product_type": "consignado_privado",
            "customer_cpf": "111.222.333-44",
            "customer_age": 15,
            "loan_amount": 1000.0,
            "contract_date": "2025-06-01",
            "consent_method": "digital_signature",
        })
        assert resp.status_code == 422

    def test_ingest_invalid_loan_amount(self, client):
        resp = client.post("/events", json={
            "correspondent_id": "CORR-001",
            "channel": "app",
            "product_type": "consignado_privado",
            "customer_cpf": "111.222.333-44",
            "customer_age": 40,
            "loan_amount": -100.0,
            "contract_date": "2025-06-01",
            "consent_method": "digital_signature",
        })
        assert resp.status_code == 422

    def test_ingest_duplicate_returns_409(self, client, mock_event_store):
        mock_event_store.append_event.return_value = False
        resp = client.post("/events", json={
            "correspondent_id": "CORR-001",
            "channel": "correspondent",
            "product_type": "consignado_inss",
            "customer_cpf": "123.456.789-00",
            "customer_age": 65,
            "loan_amount": 5000.0,
            "contract_date": "2025-06-01T14:00:00",
            "consent_method": "video",
        })
        assert resp.status_code == 409

    def test_get_event_not_found(self, client):
        resp = client.get("/events/nonexistent-id")
        assert resp.status_code == 404

    def test_get_event_found(self, client, mock_event_store):
        mock_event_store.get_event.return_value = {
            "event_id": "abc-123",
            "correspondent_id": "CORR-001",
        }
        resp = client.get("/events/abc-123")
        assert resp.status_code == 200
        assert resp.json()["event"]["event_id"] == "abc-123"

    def test_cpf_not_stored_in_plaintext(self, client, mock_event_store):
        """CPF must be hashed — never stored as raw string."""
        captured = []
        original = mock_event_store.append_event.side_effect

        def capture(event):
            captured.append(event)
            return True

        mock_event_store.append_event.side_effect = capture
        client.post("/events", json={
            "correspondent_id": "CORR-001",
            "channel": "app",
            "product_type": "consignado_privado",
            "customer_cpf": "123.456.789-00",
            "customer_age": 40,
            "loan_amount": 5000.0,
            "contract_date": "2025-06-01",
            "consent_method": "digital_signature",
        })
        assert len(captured) == 1
        event = captured[0]
        assert "123.456.789-00" not in event.customer_cpf_hash
        assert len(event.customer_cpf_hash) == 64  # SHA-256 hex
