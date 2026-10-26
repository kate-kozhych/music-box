from __future__ import annotations

from innotter_common.logging import configure
from innotter_core.config.env import CoreEnv

ENV = CoreEnv()  # type: ignore[call-arg]  # fails fast, naming the missing variable

configure("core", ENV.log_level)

SECRET_KEY = ENV.secret_key.get_secret_value()
DEBUG = ENV.env == "dev"
ALLOWED_HOSTS = [h.strip() for h in ENV.allowed_hosts.split(",") if h.strip()] + ["127.0.0.1"]
ROOT_URLCONF = "innotter_core.config.urls"
WSGI_APPLICATION = "innotter_core.config.wsgi.application"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "rest_framework",
    "drf_spectacular",
    "innotter_core.apps.platform",
    "innotter_core.apps.users",
    "innotter_core.apps.social",
    "innotter_core.apps.profiles",
]

MIDDLEWARE = [
    "innotter_common.adapters.django.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "HOST": ENV.db_host,
        "PORT": ENV.db_port,
        "NAME": ENV.db_name,
        "USER": ENV.db_user,
        "PASSWORD": ENV.db_password.get_secret_value(),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES', time_zone='+00:00'",
            "isolation_level": "read committed",
        },
        "TEST": {"NAME": f"test_{ENV.db_name}", "CHARSET": "utf8mb4", "COLLATION": "utf8mb4_0900_ai_ci"},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en"
USE_I18N = False

APPEND_SLASH = False

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "innotter_core.apps.platform.authentication.JwtAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "innotter_common.adapters.django.problem_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": None,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Innotter Core API",
    "DESCRIPTION": "Public profiles, follows and (later) the music catalog, content and feed.",
    "VERSION": "0.2.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
}

INNOTTER_JWKS_URL = ENV.jwks_url
INNOTTER_JWT_ISSUER = ENV.jwt_issuer
INNOTTER_JWT_AUDIENCE = ENV.jwt_audience
INNOTTER_REDIS_AUTH_URL = ENV.redis_auth_url
INNOTTER_AMQP_URL = ENV.amqp_url.get_secret_value()
INNOTTER_CONSUMER_HEARTBEAT_FILE = ENV.consumer_heartbeat_file
INNOTTER_CATALOG_MODE = ENV.catalog_mode

LOGGING_CONFIG = None

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
USE_X_FORWARDED_HOST = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
