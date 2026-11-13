from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from hub.config import get_settings
from hub.db import session_for
from hub.web.admin_routes import ensure_bootstrap_admin, router as admin_router
from hub.web.data_routes import router as data_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="ManageBac Data Hub", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(data_router)
    app.include_router(admin_router)
    ensure_bootstrap_admin()

    @app.on_event("startup")
    def bootstrap_admin() -> None:
        ensure_bootstrap_admin()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        session = session_for("api")
        try:
            session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        finally:
            session.close()
        return {"status": "ready"}

    return app


app = create_app()
