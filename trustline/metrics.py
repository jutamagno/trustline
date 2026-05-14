import threading
from collections import defaultdict, deque
from typing import Deque

_lock = threading.Lock()
_counters: dict[str, float] = defaultdict(float)
_gauges: dict[str, float] = {}
_observations: dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=10_000))


def _key(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return name
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{parts}}}"


def inc(name: str, value: float = 1.0, **labels: str) -> None:
    with _lock:
        _counters[_key(name, labels)] += value


def set_gauge(name: str, value: float, **labels: str) -> None:
    with _lock:
        _gauges[_key(name, labels)] = value


def observe(name: str, value: float, **labels: str) -> None:
    with _lock:
        _observations[_key(name, labels)].append(value)


def prometheus_text() -> str:
    lines: list[str] = []
    with _lock:
        for k, v in _counters.items():
            lines.append(f"{k} {v}")
        for k, v in _gauges.items():
            lines.append(f"{k} {v}")
        for k, buf in _observations.items():
            if not buf:
                continue
            sorted_buf = sorted(buf)
            n = len(sorted_buf)
            for q, label in ((0.50, "p50"), (0.90, "p90"), (0.95, "p95"), (0.99, "p99")):
                idx = min(int(q * n), n - 1)
                lines.append(f'{k}_quantile{{quantile="{label}"}} {sorted_buf[idx]}')
            lines.append(f"{k}_count {n}")
            lines.append(f"{k}_sum {sum(sorted_buf)}")
    return "\n".join(lines) + "\n"
