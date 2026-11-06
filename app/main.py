from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from app.db import init_db
from app.routers import game, surveyor
from app.templating import templates

CDN = "https://cdn.jsdelivr.net"
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        f"style-src 'self' {CDN}",
        f"font-src {CDN}",
        f"script-src 'self' {CDN}",
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
PRIVATE_PATH_PREFIXES = ("/r/", "/g/")

# 응답자도 볼 수 있는 화면이라 서비스 목적이 드러나는 표현을 쓰지 않는다.
ERROR_COPY = {
    404: ("앗, 없는 링크예요", "주소가 정확한지 다시 확인해 주세요."),
    410: ("기간이 지난 링크예요", "링크는 만든 지 30일이 지나면 사라져요."),
    429: ("조금만 천천히요!", "요청이 너무 많았어요. 잠시 후 다시 시도해 주세요."),
}
DEFAULT_ERROR_COPY = ("잠시 문제가 생겼어요", "잠시 후 다시 시도해 주세요.")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


# 자동 API 문서는 응답자에게 서비스 의도를 드러낼 수 있어 끈다.
app = FastAPI(title="취향 밸런스", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(surveyor.router)
app.include_router(game.router)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    # 결과 링크의 토큰이 외부 쇼핑 사이트로 새지 않게 한다.
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    if request.url.path.startswith(PRIVATE_PATH_PREFIXES):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def render_http_error(request: Request, exc: HTTPException) -> HTMLResponse:
    title, message = ERROR_COPY.get(exc.status_code, DEFAULT_ERROR_COPY)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "title": title,
            "message": message,
            "noindex": True,
            "offer_new_survey": request.url.path.startswith("/r/"),
        },
        status_code=exc.status_code,
        headers=exc.headers,
    )


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
