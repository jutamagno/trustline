from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MongoDB
    mongo_uri: str = "mongodb://mongo:27017"
    mongo_db: str = "trustline"
    event_ttl_days: int = 730       # LGPD: 2 years default retention

    # PostgreSQL (audit trail + Airflow)
    postgres_url: str = "postgresql://trustline:trustline@postgres:5432/trustline"

    # Kafka
    kafka_brokers: str = "kafka:9092"
    origination_topic: str = "origination_events"
    analysis_topic: str = "analysis_results"
    eval_alerts_topic: str = "eval_alerts"

    # ElasticSearch
    es_url: str = "http://elasticsearch:9200"
    es_index_events: str = "trustline-events"
    es_index_audit: str = "trustline-audit"

    # AWS / Bedrock
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "anthropic.claude-haiku-20240307-v1:0"
    localstack_endpoint: str = ""   # set to http://localstack:4566 for local dev
    use_localstack: bool = True

    # LLM
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.0    # deterministic for consistency
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout_s: int = 60

    # Analyzer
    analyzer_version: str = "1.0.0"
    risk_high_threshold: float = 0.75
    risk_medium_threshold: float = 0.50

    # Compliance
    s3_compliance_bucket: str = "trustline-compliance"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
