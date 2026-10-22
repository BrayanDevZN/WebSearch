"""`python -m websearch`: valida o .env e sobe o servidor."""

from __future__ import annotations

import logging
import sys

import uvicorn

from websearch.api.app import create_app
from websearch.config import ConfigError, load_dotenv_settings


def main() -> int:
    try:
        settings = load_dotenv_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for warning in settings.warnings:
        logging.getLogger("websearch").warning(warning)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level.lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
