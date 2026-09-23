from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler


def _plain(value):
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return str(value)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    details = response.data
    if isinstance(exc, ValidationError):
        code = "validation_error"
        fields = (_plain(details) if isinstance(details, dict)
                  else {"non_field_errors": _plain(details)})
        message = "Проверьте параметры запроса."
    else:
        fallback_codes = {404: "not_found", 403: "permission_denied",
                          405: "method_not_allowed"}
        code = getattr(exc, "default_code", fallback_codes.get(response.status_code,
                                                                 "request_error"))
        message = (str(details.get("detail", "Ошибка запроса."))
                   if isinstance(details, dict) else str(details))
        fields = {}
    response.data = {"error": {"code": code, "message": message, "fields": fields}}
    return response
