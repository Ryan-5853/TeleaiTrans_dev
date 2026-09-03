import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .artifacts import ArtifactService
from .db import Database
from .errors import ApiError, error_detail
from .flash import FlashService
from .hub import Hub
from .routers import http as http_router
from .routers import camera as camera_router
from .routers import registry as registry_router
from .routers import ws as ws_router
from .settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.db_path)
        await db.init()
        hub = Hub(db, settings)
        await hub.load_static_config()
        artifacts = ArtifactService(db, settings.artifact_dir)
        flash = FlashService(db, hub, artifacts, settings)
        hub.on_progress = flash.on_plane_progress

        app.state.settings = settings
        app.state.db = db
        app.state.hub = hub
        app.state.artifacts = artifacts
        app.state.flash = flash

        monitor_task = asyncio.create_task(hub.monitor_planes())
        try:
            yield
        finally:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
            await hub.close_all_plane_links()
            for task in tuple(flash._tasks):
                task.cancel()
            if flash._tasks:
                await asyncio.gather(*flash._tasks, return_exceptions=True)
            await db.close()

    app = FastAPI(
        title="Ground Station API",
        version="0.2.0",
        description="局域网地面站 v2：飞机身份/claim、状态、命令、参数与 M0 固件烧录",
        lifespan=lifespan,
    )
    app.include_router(http_router.router)
    app.include_router(camera_router.router)
    app.include_router(registry_router.router)
    app.include_router(ws_router.router)

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_detail(exc))

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "service": "ground-backend", "version": "0.2.0"}

    return app


app = create_app()
