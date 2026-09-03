import asyncio
import json
import re
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..errors import ApiError
from ..hub import Hub, PlaneLink

router = APIRouter()
AIRCRAFT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _hub(websocket: WebSocket) -> Hub:
    return websocket.app.state.hub


async def _plane_ws(websocket: WebSocket, proto: int) -> None:
    aircraft_id = websocket.query_params.get("aircraft_id", "")
    if not AIRCRAFT_ID_RE.match(aircraft_id or ""):
        await websocket.close(code=4400, reason="invalid aircraft_id")
        return
    hub = _hub(websocket)
    await websocket.accept()

    old = hub.planes.get(aircraft_id)
    if old is not None:
        hub.planes.pop(aircraft_id, None)
        try:
            await old.ws.close(code=4001, reason="replaced by new connection")
        except Exception:  # noqa: BLE001
            pass
    link = PlaneLink(websocket, aircraft_id, hub, proto=proto)
    hub.planes[aircraft_id] = link

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                await link.send_json(
                    {
                        "v": 1,
                        "type": "error",
                        "ts": __import__("time").time(),
                        "payload": {"code": "bad_json", "message": "JSON 解析失败"},
                    }
                )
                continue
            if not isinstance(obj, dict) or obj.get("v", 1) != proto:
                await link.send_json(
                    {
                        "v": proto,
                        "type": "error",
                        "ts": __import__("time").time(),
                        "payload": {
                            "code": "bad_envelope",
                            "message": f"非法消息信封（期望 v{proto}）",
                        },
                    }
                )
                continue
            await hub.handle_plane_message(link, obj)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        await hub.on_plane_disconnected(aircraft_id, link)


@router.websocket("/plane/v1/ws")
async def plane_ws_v1(websocket: WebSocket) -> None:
    await _plane_ws(websocket, proto=1)


@router.websocket("/plane/v2/ws")
async def plane_ws_v2(websocket: WebSocket) -> None:
    await _plane_ws(websocket, proto=2)


@router.websocket("/ui/v1/ws")
async def ui_ws(websocket: WebSocket) -> None:
    hub = _hub(websocket)
    await websocket.accept()
    hub.ui_clients.add(websocket)
    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "snapshot",
                    "payload": {"aircraft": await hub.fleet_snapshot()},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.ui_clients.discard(websocket)
