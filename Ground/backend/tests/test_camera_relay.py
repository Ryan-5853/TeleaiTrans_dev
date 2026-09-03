import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


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


class FakeCameraHandler(BaseHTTPRequestHandler):
    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            html = (
                "<html><head><title>fake cam</title></head><body>"
                '<img id="stream" src="/stream" alt="stream">'
                "<script>fetch('/api/controls');</script>"
                "</body></html>"
            )
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\nFAKEJPEG\r\n")
            self.wfile.flush()
        elif self.path == "/api/controls":
            self._json({"controls": [{"id": "0x00980901", "name": "brightness"}],
                        "camera": {"device": "/dev/video0", "width": 640, "height": 480}})
        elif self.path == "/api/process":
            self._json({"enabled": False, "threshold": 0})
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path == "/api/control":
            self._json({"ok": True, "id": "0x1", "value": 128})
        elif self.path == "/api/xu/exposure":
            self._json({"ok": True, "available": True})
        else:
            self._json({"ok": True})

    def log_message(self, fmt, *args):  # silence
        pass


@pytest.fixture()
def camera_relay_stack(tmp_path):
    camera_port = free_port()
    fake_server = ThreadingHTTPServer(("127.0.0.1", camera_port), FakeCameraHandler)
    fake_thread = threading.Thread(target=fake_server.serve_forever, daemon=True)
    fake_thread.start()

    port = free_port()
    config = tmp_path / "aircraft.yaml"
    config.write_text("aircraft: []", encoding="utf-8")
    env = {
        **os.environ,
        "GROUND_DB_PATH": str(tmp_path / "ground.db"),
        "GROUND_ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "GROUND_AIRCRAFT_CONFIG": str(config),
        "GROUND_PUBLIC_BASE": f"http://127.0.0.1:{port}",
        "GROUND_AIRCRAFT_CAMERA_PORT": str(camera_port),
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
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20.0)
    try:
        wait_until(lambda: client.get("/api/health").status_code == 200, timeout=15.0)
        registered = client.post(
            "/api/v2/aircraft",
            json={"aircraft_id": "cam-1", "name": "cam", "static_ip": "127.0.0.1"},
        )
        assert registered.status_code == 201
        yield {"port": port, "client": client}
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
        fake_server.shutdown()
        fake_server.server_close()


def test_camera_relay_ui_and_api(camera_relay_stack):
    client = camera_relay_stack["client"]
    ui = client.get("/api/v2/aircraft/cam-1/camera/ui")
    assert ui.status_code == 200, ui.text
    assert ui.text.count("/api/v2/aircraft/cam-1/camera/stream") == 1
    assert ui.text.count("/api/v2/aircraft/cam-1/camera/api/controls") == 1

    controls = client.get("/api/v2/aircraft/cam-1/camera/api/controls")
    assert controls.status_code == 200, controls.text
    assert controls.json()["camera"]["device"] == "/dev/video0"

    changed = client.post(
        "/api/v2/aircraft/cam-1/camera/api/control",
        json={"id": "0x1", "value": 128},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["value"] == 128


def test_camera_relay_stream(camera_relay_stack):
    client = camera_relay_stack["client"]
    stream = client.get("/api/v2/aircraft/cam-1/camera/stream", timeout=10.0)
    assert stream.status_code == 200
    assert "--frame" in stream.text
    assert "FAKEJPEG" in stream.text
