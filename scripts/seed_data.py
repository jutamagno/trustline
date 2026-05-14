#!/usr/bin/env python3
"""
Generates synthetic banking data:
- 500 correspondents (mix of clean, suspicious, fraudulent)
- 10,000 origination events
- ~8% flagged as fraud patterns (based on public INSS/2025 case patterns)
"""
from __future__ import annotations

import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from trustline.config import get_settings
from trustline.db.mongo import EventStore
from trustline.models import Channel, ConsentMethod, OriginationEvent, ProductType, hash_cpf

random.seed(42)

REGIONS = ["SP", "RJ", "MG", "RS", "BA", "CE", "PE", "PR", "SC", "GO"]
PRODUCTS = list(ProductType)
CHANNELS = list(Channel)
CONSENT_METHODS = list(ConsentMethod)

# Correspondent risk profiles
PROFILES = {
    "clean": {
        "weight": 0.70,
        "channels": [Channel.APP, Channel.API, Channel.CORRESPONDENT],
        "consent": [ConsentMethod.VIDEO, ConsentMethod.BIOMETRIC, ConsentMethod.DIGITAL_SIGNATURE],
        "hour_range": (8, 19),
        "flag_rate": 0.02,
        "age_range": (25, 65),
        "income_range": (2000, 12000),
    },
    "suspicious": {
        "weight": 0.20,
        "channels": [Channel.CORRESPONDENT],
        "consent": [ConsentMethod.AUDIO, ConsentMethod.WRITTEN, ConsentMethod.VIDEO],
        "hour_range": (18, 23),
        "flag_rate": 0.25,
        "age_range": (60, 80),
        "income_range": (1000, 2500),
    },
    "fraudulent": {
        "weight": 0.10,
        "channels": [Channel.CORRESPONDENT],
        "consent": [ConsentMethod.AUDIO],
        "hour_range": (0, 6),
        "flag_rate": 0.70,
        "age_range": (65, 85),
        "income_range": (1200, 1600),
    },
}


def pick_profile() -> tuple[str, dict]:
    weights = [v["weight"] for v in PROFILES.values()]
    names = list(PROFILES.keys())
    name = random.choices(names, weights=weights, k=1)[0]
    return name, PROFILES[name]


def make_correspondent_id(profile_name: str, idx: int) -> str:
    prefix = {"clean": "CORR-C", "suspicious": "CORR-S", "fraudulent": "CORR-F"}
    return f"{prefix[profile_name]}-{idx:04d}"


def generate_event(correspondent_id: str, profile: dict, base_date: datetime) -> OriginationEvent:
    h_min, h_max = profile["hour_range"]
    hour = random.randint(h_min, h_max) % 24
    minute = random.randint(0, 59)
    day_offset = random.randint(0, 29)
    occurred_at = base_date - timedelta(days=day_offset, hours=23-hour, minutes=59-minute)

    age = random.randint(*profile["age_range"])
    income = round(random.uniform(*profile["income_range"]), 2)
    channel = random.choice(profile["channels"])

    # Fraud pattern: loan_amount >> income for fraudulent correspondents
    if profile["flag_rate"] > 0.5:
        loan_amount = round(random.uniform(income * 40, income * 70), 2)
    else:
        loan_amount = round(random.uniform(income * 2, income * 15), 2)

    consent = random.choice(profile["consent"])

    return OriginationEvent(
        event_id=str(uuid.uuid4()),
        correspondent_id=correspondent_id,
        channel=channel,
        product_type=random.choice(PRODUCTS),
        customer_cpf_hash=hash_cpf(f"{random.randint(10000000000, 99999999999)}"),
        customer_age=age,
        loan_amount=loan_amount,
        contract_date=occurred_at,
        consent_method=consent,
        raw_fields={
            "prazo_meses": random.choice([12, 24, 36, 48, 60, 72, 84]),
            "taxa_juros": round(random.uniform(1.2, 2.5), 2),
        },
        occurred_at=occurred_at,
        region=random.choice(REGIONS),
        declared_income=income,
    )


def main() -> None:
    settings = get_settings()
    store = EventStore()

    print("Generating 500 correspondents + 10,000 events...")
    base_date = datetime.now(UTC)
    correspondents = []

    for i in range(500):
        profile_name, profile = pick_profile()
        cid = make_correspondent_id(profile_name, i)
        ops = random.randint(5, 50)
        correspondents.append((cid, profile, ops))

    total = 0
    for cid, profile, ops in correspondents:
        for _ in range(ops):
            if total >= 10_000:
                break
            event = generate_event(cid, profile, base_date)
            store.append_event(event)
            total += 1
        if total >= 10_000:
            break

    print(f"Seeded {total} events across {len(correspondents)} correspondents.")
    stats = store.aggregate_stats(days=30)
    print(f"Stats: {stats}")


if __name__ == "__main__":
    main()
