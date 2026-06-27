from __future__ import annotations


class AppError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "error",
        status_code: int = 400,
    ) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found", *, code: str = "not_found") -> None:
        super().__init__(message, code=code, status_code=404)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Unauthorized", *, code: str = "unauthorized") -> None:
        super().__init__(message, code=code, status_code=401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Forbidden", *, code: str = "forbidden") -> None:
        super().__init__(message, code=code, status_code=403)


class ConflictError(AppError):
    def __init__(self, message: str = "Conflict", *, code: str = "conflict") -> None:
        super().__init__(message, code=code, status_code=409)


class ValidationError(AppError):
    def __init__(self, message: str, *, code: str = "validation_error") -> None:
        super().__init__(message, code=code, status_code=422)
