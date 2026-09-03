#!/usr/bin/env python3
"""Protocol reference implementation: simulated Lyra aircraft agent.

Connects to the Ground backend over a plane-link WebSocket and provides
hello/telemetry/params/maintenance-command/m0-flash behavior so the full
stack (Ground + WebUI) can be exercised before a real board agent exists.
"""

import argparse
import asyncio
import hashlib
import json
import random
import sys
import time
from typing import Any

import httpx
import websockets
from websockets.exceptions import ConnectionClosed


class SimPlane:
    def __init__(
        self,
        aircraft_id: str,
        base_url: str,
        *,
        display_name: str | None = None,
        proto: int = 1,
        static_ip: str = "127.0.0.1",
    ):
        self.aircraft_id = aircraft_id
        self.base_url = base_url.rstrip("/")
        self.proto = proto
        self.static_ip = static_ip
        self.display_name = display_name or f"模拟飞机 {aircraft_id}"
        self.features = ["params", "maintenance", "m0_flash"]
        if proto == 2:
            self.features += ["camera", "dock"]
        self.params: dict[str, Any] = {
            "sim.armed": False,
            "sim.telemetry_rate_hz": 1.0,
            "sim.test_duration_s": 2.0,
        }
        self.armed = False
        self.active_test: str | None = None
        self.m0_app_version = "0.0.1"
        self.m0_crc = 0x50D71C38
        self.uptime = 0.0
        self._telemetry_task: asyncio.Task | None = None

    @property
    def ws_url(self) -> str:
        return (
            f"{self.base_url}/plane/v{self.proto}/ws"
            f"?aircraft_id={self.aircraft_id}"
        )

    async def run_forever(self) -> None:
        while True:
            try:
                async with websockets.connect(
                    self.ws_url, ping_interval=None, max_size=2**20
                ) as ws:
                    print(f"[sim:{self.aircraft_id}] connected", flush=True)
                    await self._send(
                        ws,
                        "hello",
                        {
                            "aircraft_id": self.aircraft_id,
                            "name": self.display_name,
                            "model": "sim-lyra-zero-w-amp",
                            "board": "sim-rk3506",
                            "agent_version": "0.1.0",
                            "features": self.features,
                            **(
                                {
                                    "ip": self.static_ip,
                                    "fcu": {
                                        "link": "up" if self.proto == 2 else "down",
                                        "armed": self.armed,
                                        "mode": "SIM",
                                    },
                                    "camera": [{"device": "/dev/video-sim"}],
                                    "dock": {"profile": None, "state": "unconfigured"},
                                }
                                if self.proto == 2
                                else {}
                            ),
                            "m0": {
                                "status": "APP1",
                                "app_version": self.m0_app_version,
                                "crc": f"0x{self.m0_crc:08X}",
                            },
                        },
                        self.proto,
                    )
                    self._telemetry_task = asyncio.create_task(
                        self._telemetry_loop(ws)
                    )
                    try:
                        async for raw in ws:
                            await self._handle(ws, json.loads(raw))
                    finally:
                        if self._telemetry_task is not None:
                            self._telemetry_task.cancel()
            except (ConnectionClosed, OSError) as exc:
                print(f"[sim:{self.aircraft_id}] disconnected: {exc}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[sim:{self.aircraft_id}] error: {exc!r}", flush=True)
            await asyncio.sleep(3.0)

    @staticmethod
    async def _send(
        ws: Any, typ: str, payload: dict[str, Any], proto: int = 1
    ) -> None:
        await ws.send(
            json.dumps(
                {"v": proto, "type": typ, "ts": time.time(), "payload": payload},
                ensure_ascii=False,
            )
        )

    async def _telemetry_loop(self, ws: Any) -> None:
        while True:
            rate = float(self.params.get("sim.telemetry_rate_hz", 1.0))
            rate = max(0.2, min(rate, 20.0))
            self.uptime += 1.0 / rate
            await self._send(
                ws,
                "telemetry",
                {
                    "uptime_s": round(self.uptime, 1),
                    "board": {
                        "cpu_pct": round(random.uniform(2, 18), 1),
                        "mem_pct": round(random.uniform(12, 30), 1),
                        "temp_c": round(random.uniform(38, 52), 1),
                    },
                    "m0": {
                        "status": "APP1",
                        "app_version": self.m0_app_version,
                        "crc": f"0x{self.m0_crc:08X}",
                        "armed": self.armed,
                        "active_test": self.active_test,
                    },
                    **(
                        {
                            "fcu": {
                                "link": "up" if self.proto == 2 else "down",
                                "armed": self.armed,
                                "mode": "SIM",
                            },
                            "peripherals": {
                                "camera": {"devices": [{"device": "/dev/video-sim"}]},
                                "dock": {"profile": None, "state": "unconfigured"},
                            },
                        }
                        if self.proto == 2
                        else {}
                    ),
                },
                self.proto,
            )
            await asyncio.sleep(1.0 / rate)

    async def _handle(self, ws: Any, obj: dict[str, Any]) -> None:
        typ = obj.get("type")
        payload = obj.get("payload") or {}
        if typ == "ping":
            return
        if typ == "command":
            await self._handle_command(ws, payload)
        elif typ == "flash_start":
            asyncio.create_task(self._handle_flash(ws, payload))
        elif typ == "error":
            print(f"[sim:{self.aircraft_id}] server error: {payload}", flush=True)

    async def _handle_command(self, ws: Any, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("request_id") or "")
        op = str(payload.get("op") or "")
        params = payload.get("params") or {}
        try:
            ok, data = await self._run_op(op, params)
        except Exception as exc:  # noqa: BLE001
            ok, data = False, {"error": f"{exc!r}"}
        await self._send(
            ws,
            "result",
            {
                "request_id": request_id,
                "ok": ok,
                "error": None if ok else str(data.get("error") or "op failed"),
                "data": data,
            },
            self.proto,
        )

    async def _run_op(
        self, op: str, params: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        if op == "ping":
            return True, {"pong": True}
        if op == "arm":
            if self.armed:
                return False, {"error": "already armed"}
            self.armed = True
            self.params["sim.armed"] = True
            return True, {"armed": True}
        if op == "disarm":
            self.armed = False
            self.active_test = None
            self.params["sim.armed"] = False
            return True, {"armed": False}
        if op == "motor_test":
            if self.active_test:
                return False, {"error": f"test {self.active_test} already running"}
            duration = float(params.get("duration_s", self.params.get(
                "sim.test_duration_s", 2.0)))
            self.active_test = "motor"
            await asyncio.sleep(max(0.1, min(duration, 10.0)))
            self.active_test = None
            return True, {"test": "motor", "result": "passed"}
        if op == "servo_test":
            if self.active_test:
                return False, {"error": f"test {self.active_test} already running"}
            duration = float(params.get("duration_s", self.params.get(
                "sim.test_duration_s", 2.0)))
            self.active_test = "servo"
            await asyncio.sleep(max(0.1, min(duration, 10.0)))
            self.active_test = None
            return True, {"test": "servo", "result": "passed"}
        if op == "stop_test":
            self.active_test = None
            return True, {"stopped": True}
        if op == "camera.snapshot":
            return True, {
                "mime": "image/jpeg",
                "width": 1,
                "height": 1,
                "bytes": 4,
                "data_b64": "AQIDBA==",
            }
        if op == "camera.params.read":
            return True, {"params": {"brightness": 128}}
        if op == "camera.params.write":
            return True, {"params": params.get("values") or {}}
        if op == "dock.status":
            return True, {"profile": None, "state": "unconfigured"}
        if op == "dock.command":
            return False, {"error": "dock profile unconfigured"}
        if op == "params.read":
            keys = params.get("keys") or []
            values = {
                k: v for k, v in self.params.items() if not keys or k in keys
            }
            return True, {"params": values}
        if op == "params.write":
            values = params.get("values") or {}
            if not isinstance(values, dict):
                return False, {"error": "values must be object"}
            self.params.update(values)
            return True, {"params": values}
        return False, {"error": f"unsupported_op {op}"}

    async def _handle_flash(self, ws: Any, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("request_id") or "")
        artifact_id = str(payload.get("artifact_id") or "")
        url = str(payload.get("url") or "")
        expected_sha = str(payload.get("sha256") or "")
        try:
            async def report(percent: int, phase: str, message: str) -> None:
                await self._send(
                    ws,
                    "progress",
                    {
                        "request_id": request_id,
                        "artifact_id": artifact_id,
                        "percent": percent,
                        "phase": phase,
                        "message": message,
                    },
                    self.proto,
                )

            await report(5, "downloading", "开始下载固件")
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.content
            await report(50, "verifying", f"已下载 {len(data)} 字节")
            digest = hashlib.sha256(data).hexdigest()
            if expected_sha and digest != expected_sha:
                raise RuntimeError(
                    f"sha256 mismatch {digest} != {expected_sha}"
                )
            if len(data) < 24 or data[:4] != b"\x50\x41\x30\x4d":
                raise RuntimeError("not an M0AP image")
            await report(80, "flashing", "M0 应用区写入中（模拟）")
            await asyncio.sleep(0.3)
            self.m0_app_version = f"sim-{artifact_id[:8]}"
            self.m0_crc = int.from_bytes(hashlib.sha256(data).digest()[:4], "little")
            await report(100, "started", "RESULT OK load+verify+start complete")
            await self._send(
                ws,
                "result",
                {
                    "request_id": request_id,
                    "ok": True,
                    "error": None,
                    "data": {
                        "artifact_id": artifact_id,
                        "m0_app_version": self.m0_app_version,
                    },
                },
                self.proto,
            )
        except Exception as exc:  # noqa: BLE001
            await self._send(
                ws,
                "result",
                {
                    "request_id": request_id,
                    "ok": False,
                    "error": str(exc),
                    "data": {},
                },
                self.proto,
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ground 模拟飞机")
    parser.add_argument("--base", default="ws://127.0.0.1:8000")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--prefix", default="sim")
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--proto", type=int, default=1, choices=(1, 2))
    parser.add_argument("--static-ip", default="127.0.0.1")
    parser.add_argument(
        "--claim-code",
        default="",
        help="先在 Ground 登记/生成 claim，再用 claim 码领取身份后以 v2 接入",
    )
    args = parser.parse_args()

    static_ip = args.static_ip
    if args.ids:
        ids = list(args.ids)
    elif args.claim_code:
        http_base = args.base.replace("ws://", "http://").replace("wss://", "https://")
        with httpx.Client(base_url=http_base, timeout=10.0) as client:
            response = client.post(
                "/api/v2/claims/redeem",
                json={
                    "code": args.claim_code,
                    "hw": {"board": "sim-rk3506", "model": "sim-lyra", "agent_version": "0.1.0"},
                },
            )
            response.raise_for_status()
            identity = response.json()["identity"]
        ids = [identity["aircraft_id"]]
        static_ip = identity.get("static_ip") or static_ip
    else:
        ids = [
            f"{args.prefix}-{i:02d}"
            for i in range(args.start_index, args.start_index + args.count)
        ]
    planes = [
        SimPlane(
            aircraft_id,
            args.base,
            display_name=f"模拟飞机 {aircraft_id.upper()}",
            proto=args.proto,
            static_ip=static_ip,
        )
        for aircraft_id in ids
    ]
    try:
        await asyncio.gather(*(plane.run_forever() for plane in planes))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
