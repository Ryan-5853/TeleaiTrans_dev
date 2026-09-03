import asyncio
import json
import re
import time
from typing import Any

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse

from ..artifacts import ArtifactService
from ..errors import ApiError
from ..flash import FlashService
from ..hub import Hub
from ..schemas import CommandRequest, FlashRequest, ParamWriteRequest

router = APIRouter(prefix="/api")

PARAM_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _hub(request: Request) -> Hub:
    return request.app.state.hub


def _artifacts(request: Request) -> ArtifactService:
    return request.app.state.artifacts


def _flash(request: Request) -> FlashService:
    return request.app.state.flash


def _validate_param_values(values: dict[str, Any]) -> None:
    if not isinstance(values, dict):
        raise ApiError(422, "bad_params", "values 必须是键值对象")
    if len(values) > 200:
        raise ApiError(422, "too_many_params", "单次参数数量不能超过 200")
    for key, value in values.items():
        if not isinstance(key, str) or not PARAM_KEY_RE.match(key):
            raise ApiError(422, "bad_param_key", f"非法参数名: {key!r}")
        if not isinstance(value, (bool, int, float, str)):
            raise ApiError(
                422, "bad_param_type", f"参数 {key} 只支持 bool/int/float/str"
            )
        if isinstance(value, str) and len(value) > 4096:
            raise ApiError(422, "bad_param_size", f"参数 {key} 过长")


@router.get("/aircraft")
async def list_aircraft(request: Request) -> dict[str, Any]:
    return {"aircraft": await _hub(request).fleet_snapshot()}


@router.get("/aircraft/{aircraft_id}")
async def get_aircraft(aircraft_id: str, request: Request) -> dict[str, Any]:
    item = await _hub(request).aircraft_view(aircraft_id)
    if item is None:
        raise ApiError(404, "aircraft_not_found", f"飞机 {aircraft_id} 不存在")
    return {"aircraft": item}


@router.post("/aircraft/{aircraft_id}/commands", status_code=202)
async def send_command(
    aircraft_id: str, body: CommandRequest, request: Request
) -> dict[str, Any]:
    hub = _hub(request)
    request_id, _ = await hub.dispatch_command(
        aircraft_id, body.op, body.params
    )
    await hub.record_event(
        aircraft_id,
        "info",
        f"command.{body.op}",
        f"下发维护命令 {body.op}",
        {"request_id": request_id, "params": body.params},
    )
    return {
        "aircraft_id": aircraft_id,
        "op": body.op,
        "request_id": request_id,
        "accepted": True,
    }


async def _param_roundtrip(
    hub: Hub,
    aircraft_id: str,
    op: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    link = hub.planes.get(aircraft_id)
    if link is None:
        raise ApiError(409, "plane_offline", f"飞机 {aircraft_id} 未接入 plane-link")
    if not link.has_feature("params"):
        raise ApiError(
            409,
            "capability_unavailable",
            f"飞机 {aircraft_id} 的 plane-link 未声明 params 能力",
        )
    request_id, fut = await hub.dispatch_command(aircraft_id, op, payload)
    try:
        result = await asyncio.wait_for(
            fut, timeout=hub.settings.param_wait_timeout_s
        )
    except asyncio.TimeoutError as exc:
        link.drop(request_id)
        raise ApiError(504, "plane_timeout", "飞机未在超时时间内返回参数结果") from exc
    if not result.get("ok"):
        raise ApiError(
            502,
            "plane_error",
            str(result.get("error") or "飞机报告参数操作失败"),
        )
    return result.get("data") or {}


@router.get("/aircraft/{aircraft_id}/params")
async def get_params(aircraft_id: str, request: Request) -> dict[str, Any]:
    hub = _hub(request)
    data = await _param_roundtrip(hub, aircraft_id, "params.read", {"keys": []})
    raw = data.get("params") or {}
    if not isinstance(raw, dict):
        raise ApiError(502, "plane_error", "飞机返回的 params 不是对象")
    now = time.time()
    await hub.db.execute(
        'INSERT INTO "params" (aircraft_id, data, updated_at) VALUES (?, ?, ?) '
        "ON CONFLICT(aircraft_id) DO UPDATE SET data = excluded.data, "
        "updated_at = excluded.updated_at",
        (aircraft_id, json.dumps(raw, ensure_ascii=False), now),
    )
    return {"params": raw}


@router.put("/aircraft/{aircraft_id}/params")
async def put_params(
    aircraft_id: str, body: ParamWriteRequest, request: Request
) -> dict[str, Any]:
    _validate_param_values(body.values)
    hub = _hub(request)
    data = await _param_roundtrip(
        hub, aircraft_id, "params.write", {"values": body.values}
    )
    raw = data.get("params") or body.values
    if not isinstance(raw, dict):
        raise ApiError(502, "plane_error", "飞机返回的 params 不是对象")
    now = time.time()
    await hub.db.execute(
        'INSERT INTO "params" (aircraft_id, data, updated_at) VALUES (?, ?, ?) '
        "ON CONFLICT(aircraft_id) DO UPDATE SET data = excluded.data, "
        "updated_at = excluded.updated_at",
        (aircraft_id, json.dumps(raw, ensure_ascii=False), now),
    )
    await hub.record_event(
        aircraft_id,
        "info",
        "params.write",
        "参数已写入",
        {"values": body.values},
    )
    return {"params": raw}


@router.post("/artifacts")
async def upload_artifact(request: Request, file: UploadFile) -> dict[str, Any]:
    settings = request.app.state.settings
    data = await file.read(settings.max_artifact_bytes + 1)
    if not data:
        raise ApiError(422, "empty_upload", "上传内容为空")
    if len(data) > settings.max_artifact_bytes:
        raise ApiError(413, "artifact_too_large", "固件文件过大")
    artifact = await _artifacts(request).store(file.filename or "app.bin", data)
    return {"artifact": artifact}


@router.get("/artifacts")
async def list_artifacts(request: Request) -> dict[str, Any]:
    return {"artifacts": await _artifacts(request).list()}


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str, request: Request) -> FileResponse:
    artifact = await _artifacts(request).get(artifact_id)
    return FileResponse(
        _artifacts(request).path_for(artifact_id),
        media_type="application/octet-stream",
        filename=artifact["filename"],
    )


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str, request: Request) -> dict[str, Any]:
    hub = _hub(request)
    await _artifacts(request).delete(artifact_id)
    await hub.record_event(
        None, "info", "artifact.deleted", f"固件产物 {artifact_id} 已删除"
    )
    return {"deleted": artifact_id}


@router.post("/aircraft/{aircraft_id}/flash", status_code=202)
async def start_flash(
    aircraft_id: str, body: FlashRequest, request: Request
) -> dict[str, Any]:
    job = await _flash(request).start(aircraft_id, body.artifact_id)
    return {"job": job, "accepted": True}


@router.get("/flash-jobs")
async def list_flash_jobs(request: Request) -> dict[str, Any]:
    aircraft_id = request.query_params.get("aircraft_id")
    return {"jobs": await _flash(request).list_jobs(aircraft_id=aircraft_id)}


@router.get("/flash-jobs/{job_id}")
async def get_flash_job(job_id: str, request: Request) -> dict[str, Any]:
    return {"job": await _flash(request).get(job_id)}


@router.get("/events")
async def list_events(request: Request) -> dict[str, Any]:
    limit_raw = request.query_params.get("limit", "200")
    try:
        limit = max(1, min(int(limit_raw), 1000))
    except ValueError:
        raise ApiError(422, "bad_limit", "limit 必须是整数") from None
    rows = await _hub(request).db.query_all(
        "SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
    )
    events = []
    for row in rows:
        try:
            data = json.loads(row["data"] or "{}")
        except json.JSONDecodeError:
            data = {}
        events.append({**row, "data": data})
    return {"events": events}
