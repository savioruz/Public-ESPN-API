def api_key_preprocessor(endpoints, result):
    result.setdefault("components", {}).setdefault("securitySchemes", {})
    result["components"]["securitySchemes"]["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "API key required. Set via API_KEY env var.",
    }
    result.setdefault("security", [])
    result["security"] = [{"ApiKeyAuth": []}]
    return result
