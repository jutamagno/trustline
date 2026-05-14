from __future__ import annotations

import json
import logging
import re
import threading
import time
from enum import Enum
from functools import lru_cache
from typing import Any

import boto3

from trustline.config import get_settings

logger = logging.getLogger(__name__)

# Bedrock pricing for Claude Haiku (USD per 1k tokens, as of 2024)
_INPUT_COST_PER_1K = 0.00025
_OUTPUT_COST_PER_1K = 0.00125


class _CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF = "half"


class BedrockClient:
    """
    AWS Bedrock client with LocalStack fallback and circuit breaker.
    Uses Claude Haiku for low latency and cost.
    """

    def __init__(self) -> None:
        settings = get_settings()
        kwargs: dict[str, Any] = {
            "service_name": "bedrock-runtime",
            "region_name": settings.aws_region,
        }
        if settings.use_localstack and settings.localstack_endpoint:
            kwargs["endpoint_url"] = settings.localstack_endpoint
            logger.info("bedrock_using_localstack",
                        extra={"endpoint": settings.localstack_endpoint})

        self._client = boto3.client(**kwargs)
        self._model_id = settings.bedrock_model_id

        # Circuit breaker state
        self._cb_lock = threading.Lock()
        self._cb_state = _CBState.CLOSED
        self._cb_failures = 0
        self._cb_threshold = settings.circuit_breaker_threshold
        self._cb_timeout = settings.circuit_breaker_timeout_s
        self._cb_opened_at: float = 0.0

        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @property
    def estimated_cost_usd(self) -> float:
        return (
            self.total_input_tokens / 1000 * _INPUT_COST_PER_1K
            + self.total_output_tokens / 1000 * _OUTPUT_COST_PER_1K
        )

    def _cb_allow(self) -> bool:
        with self._cb_lock:
            if self._cb_state == _CBState.CLOSED:
                return True
            if self._cb_state == _CBState.OPEN:
                if time.monotonic() - self._cb_opened_at >= self._cb_timeout:
                    self._cb_state = _CBState.HALF
                    return True
                return False
            # HALF: probe 20% of traffic
            import random
            return random.random() < 0.2

    def _cb_success(self) -> None:
        with self._cb_lock:
            self._cb_failures = 0
            self._cb_state = _CBState.CLOSED

    def _cb_failure(self) -> None:
        with self._cb_lock:
            self._cb_failures += 1
            if self._cb_failures >= self._cb_threshold:
                self._cb_state = _CBState.OPEN
                self._cb_opened_at = time.monotonic()
                logger.warning("circuit_breaker_opened",
                               extra={"failures": self._cb_failures})

    def invoke(self, prompt: str, max_tokens: int | None = None) -> str:
        settings = get_settings()
        max_tokens = max_tokens or settings.llm_max_tokens

        if not self._cb_allow():
            logger.warning("circuit_breaker_rejected")
            return ""

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": settings.llm_temperature,
            "messages": [{"role": "user", "content": prompt}],
        })

        try:
            t0 = time.perf_counter()
            resp = self._client.invoke_model(
                modelId=self._model_id,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            payload = json.loads(resp["body"].read())

            usage = payload.get("usage", {})
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)

            text = payload["content"][0]["text"]
            self._cb_success()

            logger.info(
                "bedrock_invoke",
                extra={
                    "latency_ms": round(latency_ms, 1),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                },
            )
            return text

        except Exception as exc:
            self._cb_failure()
            logger.error("bedrock_error", extra={"error": str(exc)})
            raise

    def invoke_json(self, prompt: str, max_tokens: int | None = None) -> dict:
        """Invoke and parse JSON from the response. Retries once on parse failure."""
        for attempt in range(2):
            try:
                raw = self.invoke(prompt, max_tokens)
                return self._extract_json(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt == 1:
                    logger.error("json_parse_failed", extra={"error": str(exc), "raw": raw[:200]})
                    return {}
        return {}

    @staticmethod
    def _extract_json(text: str) -> dict:
        # Try markdown code block first
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        # Try bare JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"No JSON found in: {text[:200]}")


@lru_cache(maxsize=1)
def get_bedrock_client() -> BedrockClient:
    return BedrockClient()
