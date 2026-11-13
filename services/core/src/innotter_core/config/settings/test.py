from innotter_core.config.settings.base import *  # noqa: F403

DEBUG = False

# No worker runs during tests; on-commit scheduling must not reach a real broker.
# Relay behaviour is tested by calling publish_pending() directly.
CELERY_BROKER_URL = "memory://"
CELERY_BEAT_SCHEDULE = {}
