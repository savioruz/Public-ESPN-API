"""Core middleware for request handling."""

import time
import uuid
from collections.abc import Callable

import structlog
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import Resolver404, resolve

logger = structlog.get_logger(__name__)


class APIKeyMiddleware:
    """Reject requests missing or having an invalid X-API-Key header."""

    EXEMPT_PATHS = ("/healthz", "/api/schema/", "/api/docs/", "/api/redoc/")

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        api_key_setting = getattr(settings, "API_KEY", None)
        if api_key_setting and not request.path.startswith(self.EXEMPT_PATHS):
            try:
                resolve(request.path_info)
            except Resolver404:
                return self.get_response(request)

            api_key = request.headers.get("X-Api-Key")
            if not api_key:
                return JsonResponse(
                    {
                        "error": {
                            "code": "not_authenticated",
                            "message": "X-API-Key header is required",
                            "status": 401,
                        }
                    },
                    status=401,
                )
            if api_key != api_key_setting:
                return JsonResponse(
                    {
                        "error": {
                            "code": "authentication_failed",
                            "message": "Invalid API key",
                            "status": 401,
                        }
                    },
                    status=401,
                )
        return self.get_response(request)


class RequestIDMiddleware:
    """Add unique request ID to each request for tracing."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Get or generate request ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.request_id = request_id  # type: ignore[attr-defined]

        # Bind request ID to structlog context
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = self.get_response(request)

        # Add request ID to response headers
        response["X-Request-ID"] = request_id

        return response


class StructuredLoggingMiddleware:
    """Log request/response information in a structured format."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Skip logging for health checks
        if request.path == "/healthz":
            return self.get_response(request)

        start_time = time.perf_counter()

        # Log incoming request
        logger.info(
            "request_started",
            method=request.method,
            path=request.path,
            query_params=dict(request.GET),
            user_agent=request.headers.get("User-Agent", ""),
        )

        response = self.get_response(request)

        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Log response
        logger.info(
            "request_finished",
            method=request.method,
            path=request.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )

        return response
