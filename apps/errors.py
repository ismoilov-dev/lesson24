"""Shared business-logic error and its DRF mapping.

Services raise `ServiceError`; views stay thin and the exception handler turns it into
`400 {"detail": ..., "code": ...}`.
"""

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class ServiceError(Exception):
    def __init__(self, message: str, code: str = "invalid", status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def exception_handler(exc, context):
    if isinstance(exc, ServiceError):
        return Response({"detail": exc.message, "code": exc.code}, status=exc.status)
    return drf_exception_handler(exc, context)
