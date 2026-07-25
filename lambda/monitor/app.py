from __future__ import annotations

import logging
from typing import Any

from hermes_notification_chaine_dancre.bootstrap import build_check_restocks_from_env


logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    use_case, config = build_check_restocks_from_env()
    result = use_case.execute(config).to_dict()
    logger.info("Monitor finished: %s", result)
    return result
