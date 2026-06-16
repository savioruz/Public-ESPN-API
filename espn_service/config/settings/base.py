"""Base settings for espn_service project.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/topics/settings/
"""

import os
from pathlib import Path
from typing import Any

import environ
import structlog

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Initialize django-environ
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
    API_KEY=(str, None),
)

# Read .env file if it exists
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY", default="django-insecure-change-me-in-production")

API_KEY = env("API_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG")

ALLOWED_HOSTS: list[str] = env.list("ALLOWED_HOSTS", default=[])


# Application definition
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
]

LOCAL_APPS = [
    "apps.core",
    "apps.espn",
    "apps.ingest",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "apps.core.middleware.APIKeyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "apps.core.middleware.StructuredLoggingMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases
DATABASES = {
    "default": env.db("DATABASE_URL", default="sqlite:///db.sqlite3"),
}


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Django REST Framework
REST_FRAMEWORK: dict[str, Any] = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "EXCEPTION_HANDLER": "apps.core.exceptions.custom_exception_handler",
}


# DRF Spectacular (OpenAPI/Swagger)
SPECTACULAR_SETTINGS = {
    "TITLE": "ESPN Service API",
    "DESCRIPTION": "Production-ready REST API for ESPN sports data ingestion and querying",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1/",
    "TAGS": [
        {"name": "Teams", "description": "Team data operations"},
        {"name": "Events", "description": "Event/game data operations"},
        {"name": "Ingest", "description": "ESPN data ingestion endpoints"},
        {"name": "Health", "description": "Health check endpoints"},
    ],
    "SECURITY": [{"ApiKeyAuth": []}],
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
            },
        },
    },
}


# CORS settings
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=False)


# Cache settings
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}


# Celery settings
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes
CELERY_BEAT_SCHEDULE = {
    # Scoreboards — re-ingest ALL configured leagues every 5 min (today + yesterday
    # UTC) so live games advance to `final` instead of freezing. Covers nba/nfl too
    # (they're in ALL_LEAGUES_CONFIG), replacing the old per-league hourly beats.
    "refresh-all-scoreboards-5min": {
        "task": "apps.ingest.tasks.refresh_all_scoreboards_task",
        "schedule": 300.0,  # Every 5 minutes (tunable)
    },
    # Unstick — re-ingest only games past kickoff still stuck scheduled/in_progress
    # (their ESPN date bucket fell outside the 5-min today+yesterday window), every
    # 10 min, re-fetching each event's date AND date-1 to self-heal ET bucketing.
    "unstick-scoreboards-10min": {
        "task": "apps.ingest.tasks.unstick_scoreboards_task",
        "schedule": 600.0,  # Every 10 minutes
    },
    # Teams — refreshed weekly (rosters/logos change infrequently)
    "refresh-teams-weekly": {
        "task": "apps.ingest.tasks.refresh_all_teams_task",
        "schedule": 86400.0 * 7,  # Weekly
    },
    # News — ingested every 30 minutes across all leagues
    "refresh-all-news-30min": {
        "task": "apps.ingest.tasks.refresh_all_news_task",
        "schedule": 1800.0,  # Every 30 minutes
    },
    # Injuries — refreshed every 4 hours (snapshot replacement)
    "refresh-all-injuries-4h": {
        "task": "apps.ingest.tasks.refresh_all_injuries_task",
        "schedule": 14400.0,  # Every 4 hours
    },
    # Transactions — refreshed every 6 hours
    "refresh-all-transactions-6h": {
        "task": "apps.ingest.tasks.refresh_all_transactions_task",
        "schedule": 21600.0,  # Every 6 hours
    },
}


# ESPN Client settings
ESPN_CLIENT = {
    # Domain URLs — override in .env if needed
    "SITE_API_BASE_URL": env("ESPN_SITE_API_BASE_URL", default="https://site.api.espn.com"),
    "CORE_API_BASE_URL": env("ESPN_CORE_API_BASE_URL", default="https://sports.core.api.espn.com"),
    "WEB_V3_API_BASE_URL": env("ESPN_WEB_V3_API_BASE_URL", default="https://site.web.api.espn.com"),
    "CDN_API_BASE_URL": env("ESPN_CDN_API_BASE_URL", default="https://cdn.espn.com"),
    "NOW_API_BASE_URL": env("ESPN_NOW_API_BASE_URL", default="https://now.core.api.espn.com"),
    # Request behaviour
    "TIMEOUT": env.float("ESPN_TIMEOUT", default=30.0),
    "MAX_RETRIES": env.int("ESPN_MAX_RETRIES", default=3),
    "RETRY_BACKOFF": env.float("ESPN_RETRY_BACKOFF", default=1.0),
    "USER_AGENT": env(
        "ESPN_USER_AGENT",
        default="ESPN-Service/1.0 (https://github.com/espn-service)",
    ),
    "RATE_LIMIT_REQUESTS": env.int("ESPN_RATE_LIMIT_REQUESTS", default=60),
    "RATE_LIMIT_PERIOD": env.int("ESPN_RATE_LIMIT_PERIOD", default=60),
}

# Optional Vercel relay (passthrough) for ESPN requests — dodge per-IP rate limits.
# Empty = direct. When set, requests go via this URL with an `x-relay-target` header.
ESPN_VERCEL_RELAY = env("ESPN_VERCEL_RELAY", default="")


# ---------------------------------------------------------------------------
# Ingest scope — the (sport, league) pairs the periodic refresh_all_* Celery
# tasks fan out over. Trimmed to the sports the downstream app actually uses
# (soccer incl. World Cup, football, basketball) to bound DB/connection load.
# Override without a code change via:
#   INGEST_LEAGUES="soccer:fifa.world,football:nfl,basketball:nba"
# ---------------------------------------------------------------------------
_DEFAULT_INGEST_LEAGUES = [
    # Soccer (World Cup + major leagues)
    "soccer:fifa.world",
    "soccer:eng.1",
    "soccer:esp.1",
    "soccer:ger.1",
    "soccer:ita.1",
    "soccer:fra.1",
    "soccer:usa.1",
    "soccer:eng.2",
    "soccer:uefa.champions",
    "soccer:uefa.europa"
    # Football
    "football:nfl",
    # Basketball
    "basketball:nba",
]


def _parse_ingest_leagues(raw: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in raw:
        sport, _, league = item.partition(":")
        sport, league = sport.strip(), league.strip()
        if sport and league:
            pairs.append((sport, league))
    return pairs


INGEST_LEAGUES: list[tuple[str, str]] = _parse_ingest_leagues(
    env.list("INGEST_LEAGUES", default=_DEFAULT_INGEST_LEAGUES)
)


# Structured logging configuration
LOGGING_LEVEL = env("LOGGING_LEVEL", default="INFO")

timestamper = structlog.processors.TimeStamper(fmt="iso")

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.processors.JSONRenderer(),
            "foreign_pre_chain": [
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                timestamper,
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
            ],
        },
        "console": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.dev.ConsoleRenderer(),
            "foreign_pre_chain": [
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                timestamper,
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
            ],
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
        },
        "json": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOGGING_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": LOGGING_LEVEL,
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": LOGGING_LEVEL,
            "propagate": False,
        },
        "clients": {
            "handlers": ["console"],
            "level": LOGGING_LEVEL,
            "propagate": False,
        },
        "celery": {
            "handlers": ["console"],
            "level": LOGGING_LEVEL,
            "propagate": False,
        },
    },
}
