"""Logging for the service: AfterLedger's own lines from INFO up, other libraries' from WARNING up (Python's
default). On Cloud Run each line is one JSON object, so Cloud Logging files it under its severity.

    AL_LOG_LEVEL=DEBUG    more detail from AfterLedger itself (in .env or the environment)
"""
from __future__ import annotations

import json
import logging
import os

_MARK = "_afterledger"


class CloudJson(logging.Formatter):
    """The structured-logging line Cloud Logging reads from a container's output."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        return json.dumps({"severity": record.levelname, "message": message, "logger": record.name},
                          ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Adds one handler to the root logger (once), and sets AfterLedger's level."""
    root = logging.getLogger()
    if not any(getattr(h, _MARK, False) for h in root.handlers):
        handler = logging.StreamHandler()
        setattr(handler, _MARK, True)
        if os.environ.get("K_SERVICE"):           # Cloud Run sets it
            handler.setFormatter(CloudJson())
        else:
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
        root.addHandler(handler)
    logging.getLogger("afterledger").setLevel((level or "INFO").upper())
