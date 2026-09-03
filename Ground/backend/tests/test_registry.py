import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import struct
import zlib
from pathlib import Path

import httpx
import pytest
import websockets

ROOT = Path(__file__).resolve().parents[2]


def make_m0ap_image(size: int = 1024) -> bytes:
    header = struct.pack("<IIIIII", 0x4D304150, 1, size, 0x800, 0x819, 0)
    data = bytearray(header + bytes((i * 7) & 0xFF for i in range(size - 24)))
    struct.pack_into("<I", data, 20, zlib.crc32(bytes(data)) & 0xFFFFFFFF)
    return bytes(data)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(predicate, timeout: float = 20.0, interval: float = 0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            value = predicate()
        except Exception:  # noqa: BLE001
            value = None
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError("condition not met")


@pytest.fixture()
def server(tmp_path):
    port = free_port()
    config = tmp_path / "aircraft.yaml"
    config.write_text("aircraft: []", encoding="utf-8")
    env = {
        **os.environ,
        "GROUND_DB_PATH": str(tmp_path / "ground.db"),
        "GROUND_ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "GROUND_AIRCRAFT_CONFIG": str(config),
        "GROUND_PUBLIC_BASE": f"http://127.0.0.1:{port}",
        "PYTHONPATH": str(ROOT / "backend"),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
    try:
        wait_until(lambda: client.get("/api/health").status_code == 200, timeout=15.0)
        yield {"port": port, "client": client, "url": f"http://127.0.0.1:{port}"}
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()


def test_register_claim_redeem(server):
    client = server["client"]
    registered = client.post(
        "/api/v2/aircraft",
        json={
            "aircraft_id": "lyra-01",
            "name": "实验机 01",
            "model": "lyra-zero-w-amp",
            "static_ip": "192.168.8.21",
        },
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["aircraft"]["static_ip"] == "192.168.8.21"

    claim = client.post("/api/v2/claims", json={"aircraft_id": "lyra-01"})
    assert claim.status_code == 201, claim.text
    code = claim.json()["code"]

    redeemed = client.post(
        "/api/v2/claims/redeem",
        json={"code": code, "hw": {"board": "rk3506"}},
    )
    assert redeemed.status_code == 200, redeemed.text
    identity = redeemed.json()["identity"]
    assert identity["aircraft_id"] == "lyra-01"
    assert "/plane/v2/ws" in redeemed.json()["ground"]["plane_ws"]

    second = client.post("/api/v2/claims/redeem", json={"code": code, "hw": {}})
    assert second.status_code == 404
    assert client.get("/api/aircraft/lyra-01").json()["aircraft"]["claim_state"] == "claimed"


def test_v2_plane_websocket_hello(server):
    client = server["client"]
    client.post(
        "/api/v2/aircraft",
        json={"aircraft_id": "lyra-v2", "static_ip": "192.168.8.22"},
    )

    state = {}

    async def exercise() -> None:
        async with websockets.connect(
            f"ws://127.0.0.1:{server['port']}/plane/v2/ws?aircraft_id=lyra-v2"
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "v": 2,
                        "type": "hello",
                        "ts": 1.0,
                        "payload": {
                            "aircraft_id": "lyra-v2",
                            "name": "v2 机",
                            "model": "lyra",
                            "board": "rk3506",
                            "agent_version": "0.2.0",
                            "ip": "192.168.8.22",
                            "features": ["params", "m0_flash", "camera", "dock"],
                            "fcu": {"link": "down"},
                        },
                    }
                )
            )
            await asyncio.sleep(0.3)
            response = httpx.get(f"http://127.0.0.1:{server['port']}/api/aircraft", timeout=5.0)
            fleet = response.json()["aircraft"]
            state["row"] = next(item for item in fleet if item["id"] == "lyra-v2")

    asyncio.run(exercise())
    row = state["row"]
    assert row["proto"] == 2
    assert row["last_ip"] == "192.168.8.22"
    assert row["channel"] == "plane-link"


def test_v2_simulator_flash_and_camera(server, tmp_path):
    client = server["client"]
    client.post(
        "/api/v2/aircraft",
        json={"aircraft_id": "lyra-v2sim", "name": "v2 模拟机", "static_ip": "127.0.0.1"},
    )
    code = client.post("/api/v2/claims", json={"aircraft_id": "lyra-v2sim"}).json()["code"]

    simulator = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(ROOT / "simulator" / "plane_sim.py"),
            "--base",
            f"ws://127.0.0.1:{server['port']}",
            "--proto",
            "2",
            "--claim-code",
            code,
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    try:
        wait_until(
            lambda: any(
                item["id"] == "lyra-v2sim"
                and item["online"]
                and item.get("proto") == 2
                for item in client.get("/api/aircraft").json()["aircraft"]
            ),
            timeout=20.0,
        )

        async def watch_camera_result() -> dict:
            async with websockets.connect(
                f"ws://127.0.0.1:{server['port']}/ui/v1/ws"
            ) as ui:
                await ui.recv()  # initial fleet snapshot
                async with httpx.AsyncClient(timeout=10.0) as http:
                    accepted = await http.post(
                        f"http://127.0.0.1:{server['port']}/api/aircraft/lyra-v2sim/commands",
                        json={"op": "camera.snapshot", "params": {"max_bytes": 100000}},
                    )
                    assert accepted.status_code == 202, accepted.text
                for _ in range(60):
                    message = json.loads(await ui.recv())
                    if message["type"] == "result" and message["payload"].get("request_id"):
                        payload = message["payload"]
                        if payload["aircraft_id"] == "lyra-v2sim" and payload["ok"]:
                            return payload["data"]
                raise AssertionError("camera.snapshot result not received")

        data = asyncio.run(watch_camera_result())
        assert data["mime"] == "image/jpeg"

        artifact = make_m0ap_image()
        upload = client.post(
            "/api/artifacts",
            files={
                "file": (
                    "app_v2.bin",
                    artifact,
                    "application/octet-stream",
                )
            },
        )
        artifact_id = upload.json()["artifact"]["id"]
        flash = client.post(
            "/api/aircraft/lyra-v2sim/flash", json={"artifact_id": artifact_id}
        )
        assert flash.status_code == 202, flash.text
        job_id = flash.json()["job"]["id"]

        def fetch_job():
            job = client.get(f"/api/flash-jobs/{job_id}").json()["job"]
            return job if job["status"] in ("success", "failed") else None

        job = wait_until(fetch_job, timeout=30.0)
        assert job["status"] == "success", job
        assert job["percent"] == 100
    finally:
        simulator.send_signal(signal.SIGINT)
        try:
            simulator.wait(timeout=5)
        except subprocess.TimeoutExpired:
            simulator.kill()
