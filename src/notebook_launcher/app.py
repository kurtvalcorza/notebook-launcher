from __future__ import annotations

from fastapi import FastAPI

from .config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Notebook Launcher", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "host": settings.host,
            "port": settings.port,
        }

    return app


app = create_app()
