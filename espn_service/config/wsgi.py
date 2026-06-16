"""WSGI config for espn_service project."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

application = get_wsgi_application()

# Initialise OpenTelemetry for the web process (runs per gunicorn worker since
# preload_app is off). No-op unless OTEL_ENDPOINT is set.
from config.otel import configure_otel  # noqa: E402

configure_otel("web")
