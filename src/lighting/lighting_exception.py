import logging

logger = logging.getLogger(__name__)


class LightingException(Exception):
    def __init__(self, message):
        super().__init__(message)
        logger.warning("LightingException: " + message)
