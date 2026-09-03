"""Ground-forwarded camera HTTP relay.

The aircraft keeps its local camera/debug HTTP service (MJPEG stream,
V4L2 controls, Sonix XU sensor and frame-processor endpoints).  This router
opens extra HTTP connections from Ground to that service so browsers never
talk to the aircraft directly:

    GET  /api/v2/aircraft/{id}/camera/ui              old debug page (rewritten)
    GET  /api/v2/aircraft/{id}/camera/stream          MJPEG passthrough
    GET  /api/v2/aircraft/{id}/camera/api/controls    old /api/controls
    POST /api/v2/aircraft/{id}/camera/api/control     old /api/control
    ... any old path under /api/... is proxied verbatim
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from ..errors import ApiError

LOG = logging.getLogger("ground.camera")
router = APIRouter(prefix="/api/v2/aircraft", tags=["camera"])

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


async def _camera_base(request: Request, aircraft_id: str) -> str:
    hub = request.app.state.hub
    settings = request.app.state.settings
    row = await hub.db.query_one(
        "SELECT * FROM aircraft WHERE id = ?", (aircraft_id,)
    )
    if row is None:
        raise ApiError(404, "aircraft_not_found", f"飞机 {aircraft_id} 不存在")
    link = hub.planes.get(aircraft_id)
    ip = (
        link.meta.get("ip")
        if link is not None
        else None
    ) or row.get("static_ip") or row.get("last_ip")
    if not ip:
        raise ApiError(
            409,
            "camera_no_ip",
            f"飞机 {aircraft_id} 没有可用 IP（先登记 static_ip 或等待 v2 hello）",
        )
    return f"http://{ip}:{settings.aircraft_camera_port}"


def _client_timeout(settings: Any, stream: bool = False) -> httpx.Timeout:
    if stream:
        return httpx.Timeout(
            connect=settings.aircraft_camera_connect_timeout_s,
            read=None,
            write=None,
            pool=10.0,
        )
    return httpx.Timeout(
        connect=settings.aircraft_camera_connect_timeout_s,
        read=settings.aircraft_camera_timeout_s,
        write=settings.aircraft_camera_timeout_s,
        pool=10.0,
    )


def _response_headers(resp: httpx.Response) -> dict[str, str]:
    headers = {}
    for name, value in resp.headers.items():
        if name.lower() not in HOP_BY_HOP and name.lower() != "content-length":
            headers[name] = value
    return headers


async def _relay(
    request: Request,
    aircraft_id: str,
    old_path: str,
) -> Response:
    base = await _camera_base(request, aircraft_id)
    url = f"{base}/{old_path}"
    settings = request.app.state.settings
    body = await request.body()
    content_type = request.headers.get("content-type")
    headers = {"Content-Type": content_type} if content_type else {}
    try:
        async with httpx.AsyncClient(timeout=_client_timeout(settings)) as client:
            resp = await client.request(
                request.method,
                url,
                content=body if body else None,
                headers=headers or None,
                params=request.query_params,
            )
    except httpx.HTTPError as exc:
        LOG.warning("camera relay %s -> %s failed: %r", aircraft_id, url, exc)
        raise ApiError(
            502,
            "camera_unreachable",
            f"无法连接飞机摄像头服务 {base}（{exc.__class__.__name__}）",
        ) from exc
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=_response_headers(resp),
    )


async def _stream_iterator(
    request: Request, aircraft_id: str, old_path: str
) -> AsyncIterator[bytes]:
    base = await _camera_base(request, aircraft_id)
    url = f"{base}/{old_path}"
    settings = request.app.state.settings
    try:
        async with httpx.AsyncClient(timeout=_client_timeout(settings, stream=True)) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return
                async for chunk in resp.aiter_raw():
                    yield chunk
    except (httpx.HTTPError, asyncio.CancelledError):
        return


@router.get("/{aircraft_id}/camera/ui")
async def camera_ui(request: Request, aircraft_id: str) -> HTMLResponse:
    """Serve the aircraft's original debug page with paths rewritten to the
    Ground relay prefix, preserving the old UI/functionality."""

    base = await _camera_base(request, aircraft_id)
    settings = request.app.state.settings
    prefix = f"/api/v2/aircraft/{aircraft_id}/camera"
    try:
        async with httpx.AsyncClient(timeout=_client_timeout(settings)) as client:
            resp = await client.get(f"{base}/")
            resp.raise_for_status()
            html = resp.text
    except (httpx.HTTPError, ApiError) as exc:
        LOG.warning("camera ui fetch failed: %r", exc)
        return HTMLResponse(
            "<html><body><h1>摄像头调试服务不可达</h1>"
            f"<p>{base}</p></body></html>",
            status_code=502,
        )
    # Order matters: rewrite quoted /api/ paths first, then the <img> stream
    # src. Doing it the other way would re-match the freshly inserted prefix
    # and produce a doubled /api/v2/.../camera/api/v2/.../stream URL.
    html = html.replace("'/api/", f"'{prefix}/api/")
    html = html.replace('"/api/', f'"{prefix}/api/')
    html = html.replace('src="/stream"', f'src="{prefix}/stream"')
    return HTMLResponse(html)


@router.get("/{aircraft_id}/camera/stream")
async def camera_stream(request: Request, aircraft_id: str) -> StreamingResponse:
    await _camera_base(request, aircraft_id)
    return StreamingResponse(
        _stream_iterator(request, aircraft_id, "stream"),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.api_route(
    "/{aircraft_id}/camera/api/{old_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def camera_api_relay(
    request: Request, aircraft_id: str, old_path: str
) -> Response:
    return await _relay(request, aircraft_id, f"api/{old_path}")


@router.api_route(
    "/{aircraft_id}/camera/{file_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def camera_file_relay(
    request: Request, aircraft_id: str, file_path: str
) -> Response:
    return await _relay(request, aircraft_id, file_path)
