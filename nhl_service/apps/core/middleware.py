from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import Resolver404, resolve


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
                    {"error": {"code": "not_authenticated", "message": "X-API-Key header is required", "status": 401}},
                    status=401,
                )
            if api_key != api_key_setting:
                return JsonResponse(
                    {"error": {"code": "authentication_failed", "message": "Invalid API key", "status": 401}},
                    status=401,
                )
        return self.get_response(request)
