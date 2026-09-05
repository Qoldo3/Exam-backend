from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

# Single shared limiter instance. Endpoints opt in via @limiter.limit(...).
limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings.RATE_LIMIT_ENABLED,
    default_limits=[],
    config_filename="",
)
