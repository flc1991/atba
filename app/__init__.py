from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.limiter import limiter
from app.middleware import SecurityHeadersMiddleware


def create_app() -> FastAPI:
    app = FastAPI(
        title="ATBA Herding",
        description="American Tending Breeds Association website",
        docs_url="/api/docs" if settings.DEBUG else None,
        redoc_url=None,
    )

    # Rate limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Middleware (added in reverse order — last added runs first)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="atba_session",
        https_only=not settings.DEBUG,
        same_site="lax",
    )

    # Static files
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # Routers
    from app.routers.main import router as main_router
    from app.routers.auth import router as auth_router
    from app.routers.events import router as events_router
    from app.routers.trials import router as trials_router
    from app.routers.registrations import router as registrations_router
    from app.routers.admin import router as admin_router
    from app.routers.payments import router as payments_router

    app.include_router(main_router)
    app.include_router(auth_router, prefix="/auth")
    app.include_router(events_router, prefix="/events")
    app.include_router(trials_router, prefix="/events")
    app.include_router(registrations_router, prefix="/events")
    app.include_router(admin_router, prefix="/admin")
    app.include_router(payments_router, prefix="/payments")

    return app
