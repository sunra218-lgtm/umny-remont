"""Ошибки в формате RFC 9457 (application/problem+json)."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_CONTENT_TYPE = "application/problem+json"


class Problem(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers
        self.extra = extra
        super().__init__(title)


def problem_response(
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    instance: str | None = None,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {"type": f"/problems/{code}", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    if instance:
        body["instance"] = instance
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_CONTENT_TYPE, headers=headers)


_DEFAULT_TITLES = {
    400: ("bad-request", "Некорректный запрос"),
    401: ("unauthorized", "Требуется вход"),
    403: ("forbidden", "Недостаточно прав"),
    404: ("not-found", "Не найдено"),
    405: ("method-not-allowed", "Метод не поддерживается"),
    409: ("conflict", "Конфликт"),
    429: ("too-many-requests", "Слишком много запросов"),
}


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def _problem(request: Request, exc: Problem) -> JSONResponse:
        return problem_response(
            exc.status, exc.code, exc.title, exc.detail, request.url.path, exc.headers, **exc.extra
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, title = _DEFAULT_TITLES.get(exc.status_code, ("error", "Ошибка"))
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail != title else None
        return problem_response(
            exc.status_code, code, title, detail, request.url.path, getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(err.get("loc", [])), "msg": err.get("msg", ""), "type": err.get("type")}
            for err in exc.errors()
        ]
        return problem_response(
            422,
            "validation-error",
            "Проверьте введённые данные",
            None,
            request.url.path,
            errors=errors,
        )
