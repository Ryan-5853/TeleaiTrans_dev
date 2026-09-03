import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from .db import Database
from .errors import ApiError
from .settings import Settings

OnProgress = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class PlaneLink:
    def __init__(self, ws: Any, aircraft_id: str, hub: "Hub", proto: int = 1):
        self.ws = ws
        self.aircraft_id = aircraft_id
        self.hub = hub
        self.proto = proto
        self.last_seen = time.time()
        self.meta: dict[str, Any] = {}
        self.features: list[str] = []
        self.pending: dict[str, asyncio.Future] = {}

    def touch(self) -> None:
        self.last_seen = time.time()

    async def send_json(self, obj: dict[str, Any]) -> None:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        await self.ws.send_text(text)

    def new_future(self, request_id: str) -> asyncio.Future:
        fut: asyncio.Future = self.hub.loop.create_future()
        self.pending[request_id] = fut
        return fut

    def drop(self, request_id: str) -> None:
        fut = self.pending.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.cancel()

    def resolve(self, request_id: str, payload: dict[str, Any]) -> None:
        fut = self.pending.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.set_result(payload)

    def has_feature(self, feature: str) -> bool:
        return feature in self.features


class Hub:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.loop = asyncio.get_event_loop()
        self.planes: dict[str, PlaneLink] = {}
        self.ui_clients: set[Any] = set()
        self.telemetry: dict[str, dict[str, Any]] = {}
        self._telemetry_last_broadcast: dict[str, float] = {}
        self.on_progress: OnProgress | None = None

    # ---------- static fleet config ----------
    async def load_static_config(self) -> None:
        path = Path(self.settings.aircraft_config)
        if not path.exists():
            return
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in raw.get("aircraft", []) or []:
            aircraft_id = str(entry.get("id", "")).strip()
            if not aircraft_id:
                continue
            ssh = entry.get("ssh") or {}
            key_path = ssh.get("key_path") or None
            if key_path:
                key_path = str(Path(key_path).expanduser())
            await self.upsert_aircraft(
                aircraft_id,
                name=str(entry.get("name") or aircraft_id),
                model=str(entry.get("model") or ""),
                ssh_host=ssh.get("host"),
                ssh_user=ssh.get("user"),
                ssh_key_path=key_path,
            )

    async def upsert_aircraft(
        self,
        aircraft_id: str,
        name: str | None = None,
        model: str | None = None,
        ssh_host: str | None = None,
        ssh_user: str | None = None,
        ssh_key_path: str | None = None,
    ) -> None:
        now = time.time()
        await self.db.execute(
            """
            INSERT INTO aircraft
              (id, name, model, ssh_host, ssh_user, ssh_key_path,
               registered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name = COALESCE(NULLIF(excluded.name, ''), aircraft.name),
              model = COALESCE(NULLIF(excluded.model, ''), aircraft.model),
              ssh_host = COALESCE(excluded.ssh_host, aircraft.ssh_host),
              ssh_user = COALESCE(excluded.ssh_user, aircraft.ssh_user),
              ssh_key_path = COALESCE(excluded.ssh_key_path, aircraft.ssh_key_path),
              updated_at = excluded.updated_at
            """,
            (
                aircraft_id,
                name or aircraft_id,
                model or "",
                ssh_host,
                ssh_user,
                ssh_key_path,
                now,
                now,
            ),
        )

    # ---------- UI broadcast ----------
    async def broadcast_ui(self, obj: dict[str, Any]) -> None:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        if not self.ui_clients:
            return
        await asyncio.gather(
            *(self._send_ui(ws, text) for ws in tuple(self.ui_clients)),
            return_exceptions=True,
        )

    @staticmethod
    async def _send_ui(ws: Any, text: str) -> None:
        await ws.send_text(text)

    async def record_event(
        self,
        aircraft_id: str | None,
        level: str,
        code: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        row_id = await self.db.execute(
            "INSERT INTO events (ts, aircraft_id, level, code, message, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                now,
                aircraft_id,
                level,
                code,
                message,
                json.dumps(data or {}, ensure_ascii=False),
            ),
        )
        await self.broadcast_ui(
            {
                "type": "event",
                "payload": {
                    "id": row_id,
                    "ts": now,
                    "aircraft_id": aircraft_id,
                    "level": level,
                    "code": code,
                    "message": message,
                    "data": data or {},
                },
            }
        )

    # ---------- plane link management ----------
    async def handle_plane_message(
        self, link: PlaneLink, obj: dict[str, Any]
    ) -> None:
        typ = obj.get("type")
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        link.touch()

        if typ == "hello":
            features = payload.get("features") or []
            if not isinstance(features, list):
                features = []
            link.features = [str(f) for f in features if isinstance(f, str)]
            link.meta = {
                "name": str(payload.get("name") or link.aircraft_id),
                "model": str(payload.get("model") or ""),
                "board": str(payload.get("board") or ""),
                "agent_version": str(payload.get("agent_version") or ""),
                "ip": str(payload.get("ip") or ""),
                "m0": payload.get("m0") or {},
                "fcu": payload.get("fcu") or {},
                "camera": payload.get("camera") or [],
                "dock": payload.get("dock") or {},
            }
            await self.upsert_aircraft(
                link.aircraft_id,
                name=link.meta["name"],
                model=link.meta["model"],
            )
            await self.db.execute(
                "UPDATE aircraft SET last_ip = ?, channel = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    link.meta["ip"] or None,
                    f"plane-v{link.proto}",
                    time.time(),
                    link.aircraft_id,
                ),
            )
            await self.broadcast_plane(link.aircraft_id)
            await self.record_event(
                link.aircraft_id,
                "info",
                "aircraft.online",
                f"{link.meta['name']} 已上线",
                {"agent_version": link.meta["agent_version"]},
            )

        elif typ == "telemetry":
            self.telemetry[link.aircraft_id] = {
                "received_ts": time.time(),
                "payload": payload,
            }
            await self._broadcast_telemetry(link.aircraft_id)

        elif typ == "event":
            await self.record_event(
                link.aircraft_id,
                str(payload.get("level") or "info"),
                str(payload.get("code") or "aircraft.event"),
                str(payload.get("message") or ""),
                payload.get("data") if isinstance(payload.get("data"), dict) else None,
            )

        elif typ == "result":
            request_id = str(payload.get("request_id") or "")
            await self.broadcast_ui(
                {
                    "type": "result",
                    "payload": {
                        "aircraft_id": link.aircraft_id,
                        **payload,
                    },
                }
            )
            if request_id:
                link.resolve(request_id, payload)

        elif typ == "progress":
            request_id = str(payload.get("request_id") or "")
            await self.broadcast_ui(
                {
                    "type": "progress",
                    "payload": {
                        "aircraft_id": link.aircraft_id,
                        **payload,
                    },
                }
            )
            if request_id and self.on_progress is not None:
                await self.on_progress(link.aircraft_id, request_id, payload)

    async def _broadcast_telemetry(self, aircraft_id: str) -> None:
        now = time.time()
        last = self._telemetry_last_broadcast.get(aircraft_id, 0.0)
        min_interval = 1.0 / max(self.settings.ui_telemetry_max_hz, 0.1)
        if now - last < min_interval:
            return
        self._telemetry_last_broadcast[aircraft_id] = now
        await self.broadcast_ui(
            {
                "type": "telemetry",
                "payload": {
                    "aircraft_id": aircraft_id,
                    **self.telemetry[aircraft_id],
                },
            }
        )

    async def broadcast_plane(self, aircraft_id: str) -> None:
        item = await self.aircraft_view(aircraft_id)
        if item is not None:
            await self.broadcast_ui(
                {"type": "aircraft_update", "payload": {"aircraft": item}}
            )

    async def dispatch_command(
        self, aircraft_id: str, op: str, params: dict[str, Any] | None = None
    ) -> tuple[str, asyncio.Future]:
        link = self.planes.get(aircraft_id)
        if link is None:
            raise ApiError(
                409, "plane_offline", f"飞机 {aircraft_id} 未接入 plane-link"
            )
        request_id = uuid.uuid4().hex[:24]
        fut = link.new_future(request_id)
        try:
            await link.send_json(
                {
                    "v": link.proto,
                    "type": "command",
                    "ts": time.time(),
                    "payload": {
                        "request_id": request_id,
                        "op": op,
                        "params": params or {},
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            link.drop(request_id)
            raise ApiError(502, "command_send_failed", f"发送失败: {exc}") from exc
        return request_id, fut

    async def aircraft_view(self, aircraft_id: str) -> dict[str, Any] | None:
        row = await self.db.query_one(
            "SELECT * FROM aircraft WHERE id = ?", (aircraft_id,)
        )
        if row is None:
            return None
        return self._merge_view(row)

    async def fleet_snapshot(self) -> list[dict[str, Any]]:
        rows = await self.db.query_all("SELECT * FROM aircraft ORDER BY id")
        return [self._merge_view(row) for row in rows]

    def _merge_view(self, row: dict[str, Any]) -> dict[str, Any]:
        aircraft_id = row["id"]
        link = self.planes.get(aircraft_id)
        telemetry = self.telemetry.get(aircraft_id)
        if link is not None:
            channel = "plane-link"
            online = True
            capabilities = list(link.features)
            last_seen = link.last_seen
            agent_version = link.meta.get("agent_version", "")
            m0 = link.meta.get("m0", {}) or {}
        elif row.get("ssh_host"):
            channel = "ssh"
            online = False
            capabilities = []
            last_seen = None
            agent_version = ""
            m0 = {}
        else:
            channel = "none"
            online = False
            capabilities = []
            last_seen = None
            agent_version = ""
            m0 = {}
        fcu = link.meta.get("fcu", {}) if link is not None else {}
        peripherals = (
            {
                "camera": link.meta.get("camera", []),
                "dock": link.meta.get("dock", {}),
            }
            if link is not None
            else {}
        )
        if telemetry and link is not None:
            payload = telemetry["payload"]
            m0 = payload.get("m0", m0) or m0
            fcu = payload.get("fcu", fcu) or fcu
            peripherals = payload.get("peripherals", peripherals) or peripherals
        return {
            "id": aircraft_id,
            "name": row["name"] or aircraft_id,
            "alias": row.get("alias") or row["name"] or aircraft_id,
            "model": row["model"],
            "channel": channel,
            "proto": link.proto if link is not None else None,
            "static_ip": row.get("static_ip"),
            "last_ip": row.get("last_ip"),
            "claim_state": row.get("claim_state") or "unclaimed",
            "online": online,
            "capabilities": capabilities,
            "last_seen": last_seen,
            "agent_version": agent_version,
            "m0": m0,
            "fcu": fcu,
            "peripherals": peripherals,
            "ssh": (
                {
                    "host": row["ssh_host"],
                    "user": row["ssh_user"] or "root",
                }
                if row.get("ssh_host")
                else None
            ),
            "telemetry": (telemetry["payload"] if telemetry else None),
        }

    async def monitor_planes(self) -> None:
        while True:
            await asyncio.sleep(min(5.0, self.settings.plane_heartbeat_interval_s))
            now = time.time()
            for aircraft_id, link in tuple(self.planes.items()):
                if now - link.last_seen > self.settings.plane_timeout_s:
                    try:
                        await link.ws.close(code=4002, reason="heartbeat timeout")
                    except Exception:  # noqa: BLE001
                        pass

    async def on_plane_disconnected(self, aircraft_id: str, link: PlaneLink) -> None:
        if self.planes.get(aircraft_id) is not link:
            return
        self.planes.pop(aircraft_id, None)
        self.telemetry.pop(aircraft_id, None)
        self._telemetry_last_broadcast.pop(aircraft_id, None)
        for fut in tuple(link.pending.values()):
            if not fut.done():
                fut.cancel()
        link.pending.clear()
        await self.record_event(
            aircraft_id,
            "warn",
            "aircraft.offline",
            f"飞机 {aircraft_id} 已离线",
        )
        await self.broadcast_plane(aircraft_id)

    async def close_all_plane_links(self) -> None:
        for link in tuple(self.planes.values()):
            try:
                await link.ws.close(code=4000, reason="server shutdown")
            except Exception:  # noqa: BLE001
                pass
