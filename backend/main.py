from os import getenv
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .pipeline.results import InvalidArtifact, StorageUnavailable
from .routers.auth import admin_router, auth_router
from .routers.dashboard import router


ROOT = Path(__file__).resolve().parents[1]


def create_app(data_directory: Path | None = None) -> FastAPI:
    app = FastAPI(title="Пульт доставки", version="0.1.0")
    app.state.data_directory = data_directory or Path(getenv("PULT_DATA_DIR", ROOT / "data"))
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(admin_router)

    @app.exception_handler(InvalidArtifact)
    async def invalid_artifact(_request: Request, error: InvalidArtifact) -> JSONResponse:
        return JSONResponse({"detail": f"Не удалось прочитать файл результатов {error}."}, status_code=503)

    @app.exception_handler(StorageUnavailable)
    async def database_unavailable(_request: Request, error: StorageUnavailable) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=503)

    frontend = ROOT / "frontend" / "dist"
    if (frontend / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="dashboard")
    else:
        @app.get("/", include_in_schema=False)
        def missing_frontend() -> dict:
            return {"detail": "Соберите веб-пульт командой npm --prefix frontend run build."}

    return app


app = create_app()
