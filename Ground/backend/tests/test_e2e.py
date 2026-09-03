import asyncio
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import httpx
import pytest
import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def make_m0ap_image() -> bytes:
    size = 1024
    header = struct.pack("<IIIIII", 0x4D304150, 1, size, 0x800, 0x819, 0)
    data = bytearray(
        header + bytes((i * 7) & 0xFF for i in range(size - 24))
    )
    struct.pack_into("<I", data, 20, zlib.crc32(bytes(data)) & 0xFFFFFFFF)
    return bytes(data)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(predicate, timeout: float = 20.0, interval: float = 0.2):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            value = predicate()
        except Exception as exc:  # noqa: BLE001
            last = exc
        else:
            if value:
                return value
            last = None
        time.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s: {last!r}")


@pytest.fixture()
def stack(tmp_path):
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
    server = subprocess.Popen(
        [
            sys.executable,
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
    simulator = None
    try:
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
        wait_until(
            lambda: client.get("/api/health").status_code == 200, timeout=15.0
        )
        simulator = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "simulator" / "plane_sim.py"),
                "--base",
                f"ws://127.0.0.1:{port}",
                "--ids",
                "e2e-sim",
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        wait_until(
            lambda: any(
                a["id"] == "e2e-sim" and a["online"]
                for a in client.get("/api/aircraft").json().get("aircraft", [])
            ),
            timeout=20.0,
        )
        yield {"port": port, "client": client}
    finally:
        if simulator is not None:
            simulator.send_signal(signal.SIGINT)
            try:
                simulator.wait(timeout=5)
            except subprocess.TimeoutExpired:
                simulator.kill()
        server.send_signal(signal.SIGTERM)
        try:
            server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            server.kill()
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def test_full_stack_flow(stack):
    client = stack["client"]
    fleet = client.get("/api/aircraft").json()["aircraft"]
    assert any(a["id"] == "e2e-sim" and a["online"] for a in fleet)

    def fetch_params():
        response = client.get("/api/aircraft/e2e-sim/params")
        if response.status_code != 200:
            return None
        data = response.json()
        return data if "sim.armed" in data["params"] else None

    params_resp = wait_until(fetch_params)
    assert params_resp["params"]["sim.armed"] is False

    def write_params():
        response = client.put(
            "/api/aircraft/e2e-sim/params",
            json={"values": {"sim.demo": "ok"}},
        )
        return response.json() if response.status_code == 200 else None

    write_resp = wait_until(write_params)
    assert write_resp["params"]["sim.demo"] == "ok"

    artifact_data = make_m0ap_image()
    upload = client.post(
        "/api/artifacts",
        files={
            "file": (
                "app_e2e.bin",
                artifact_data,
                "application/octet-stream",
            )
        },
    )
    assert upload.status_code == 200, upload.text
    artifact_id = upload.json()["artifact"]["id"]

    flash = client.post(
        "/api/aircraft/e2e-sim/flash", json={"artifact_id": artifact_id}
    )
    assert flash.status_code == 202, flash.text
    job_id = flash.json()["job"]["id"]

    def fetch_job():
        job = client.get(f"/api/flash-jobs/{job_id}").json()["job"]
        return job if job["status"] in ("success", "failed") else None

    job = wait_until(fetch_job, timeout=30.0)
    assert job["status"] == "success", job
    assert job["percent"] == 100

    events = client.get("/api/events?limit=50").json()["events"]
    codes = {event["code"] for event in events}
    assert "flash.success" in codes
    assert "aircraft.online" in codes


def test_ui_websocket_stream(stack):
    port = stack["port"]

    async def check() -> None:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/ui/v1/ws"
        ) as ws:
            snapshot = json.loads(await asyncio.ensure_future(ws.recv()))
            assert snapshot["type"] == "snapshot"
            planes = snapshot["payload"]["aircraft"]
            assert any(
                item["id"] == "e2e-sim" and item["online"] for item in planes
            )
            for _ in range(30):
                message = json.loads(await asyncio.ensure_future(ws.recv()))
                if message["type"] == "telemetry":
                    return
            raise AssertionError("no telemetry frame within 30 UI messages")

    asyncio.run(check())
