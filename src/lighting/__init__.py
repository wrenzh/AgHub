import logging
from .lighting import router

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(asctime)s:%(name)s:%(levelname)-8s - %(message)s")
h = logging.FileHandler("lighting.log", encoding="utf-8")
h.setFormatter(formatter)
logger.addHandler(h)

__all__ = ["router"]
