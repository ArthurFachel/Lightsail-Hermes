"""Configuracao de logging estruturado JSON para ingestao em CloudWatch."""

import logging
import sys
from pythonjsonlogger import jsonlogger


def setup_logging():
    log_handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s %(pathname)s %(lineno)d",
        rename_fields={"levelname": "severity", "asctime": "@timestamp"}
    )
    log_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = []
    root.addHandler(log_handler)
    root.setLevel(logging.INFO)

    # Silencia logs verbosos de libs externas no root
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)

    # Flask/werkzeug
    werkzeug = logging.getLogger("werkzeug")
    werkzeug.handlers = []
    werkzeug.addHandler(log_handler)

    return root
