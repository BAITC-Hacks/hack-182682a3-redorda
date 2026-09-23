from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        details = response.data
        message = details.get("detail") if isinstance(details, dict) else None
        response.data = {"error": {
            "code": getattr(exc, "default_code", "request_error"),
            "message": str(message or "Проверьте параметры запроса."),
            "fields": details if not message else {},
        }}
    return response
