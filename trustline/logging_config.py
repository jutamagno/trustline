import json
import logging
import sys
from datetime import UTC, datetime

_SKIP = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _SKIP and not k.startswith("_"):
                out[k] = v
        return json.dumps(out, default=str)


class HumanFormatter(logging.Formatter):
    _FMT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"

    def __init__(self) -> None:
        super().__init__(self._FMT, datefmt="%H:%M:%S")


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter() if json_logs else HumanFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("uvicorn.access", "kafka", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
