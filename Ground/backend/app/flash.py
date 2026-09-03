import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import paramiko

from .artifacts import ArtifactService
from .db import Database
from .errors import ApiError
from .hub import Hub
from .settings import Settings

BLOCK_RE = re.compile(r"block\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)
FAIL_MARKERS = (
    "cannot open",
    "not an M0AP image",
    "crc mismatch",
    "verify failed",
    "bootloader not ready",
    "no kernel M0 reset path",
    "RESULT FAIL",
    "ERROR:",
    "error:",
    "failed",
)


class FlashService:
    def __init__(
        self,
        db: Database,
        hub: Hub,
        artifacts: ArtifactService,
        settings: Settings,
    ):
        self.db = db
        self.hub = hub
        self.artifacts = artifacts
        self.settings = settings
        self.loop = asyncio.get_event_loop()
        self.active: dict[str, str] = {}  # aircraft_id -> job_id
        self._tasks: set[asyncio.Task] = set()

    @staticmethod
    def view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "aircraft_id": row["aircraft_id"],
            "artifact_id": row["artifact_id"],
            "status": row["status"],
            "phase": row["phase"],
            "percent": row["percent"],
            "error": row["error"],
            "log_tail": row["log_tail"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    async def get(self, job_id: str) -> dict[str, Any]:
        row = await self.db.query_one(
            "SELECT * FROM flash_jobs WHERE id = ?", (job_id,)
        )
        if row is None:
            raise ApiError(404, "job_not_found", f"烧录作业 {job_id} 不存在")
        return self.view(row)

    async def list_jobs(
        self, aircraft_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        if aircraft_id:
            rows = await self.db.query_all(
                "SELECT * FROM flash_jobs WHERE aircraft_id = ? "
                "ORDER BY started_at DESC, id LIMIT ?",
                (aircraft_id, limit),
            )
        else:
            rows = await self.db.query_all(
                "SELECT * FROM flash_jobs ORDER BY started_at DESC, id LIMIT ?",
                (limit,),
            )
        return [self.view(row) for row in rows]

    async def start(self, aircraft_id: str, artifact_id: str) -> dict[str, Any]:
        artifact = await self.artifacts.get(artifact_id)
        aircraft = await self.db.query_one(
            "SELECT * FROM aircraft WHERE id = ?", (aircraft_id,)
        )
        if aircraft is None:
            raise ApiError(404, "aircraft_not_found", f"飞机 {aircraft_id} 不存在")

        link = self.hub.planes.get(aircraft_id)
        if link is not None and link.has_feature("m0_flash"):
            mode = "plane"
        elif aircraft.get("ssh_host"):
            mode = "ssh"
        else:
            raise ApiError(
                409,
                "flash_unavailable",
                f"飞机 {aircraft_id} 没有可用烧录通道 "
                "（无 plane-link/m0_flash，也未配置 SSH）",
            )
        if aircraft_id in self.active:
            raise ApiError(
                409,
                "flash_busy",
                f"飞机 {aircraft_id} 已有进行中的烧录作业 "
                f"{self.active[aircraft_id]}",
            )

        job_id = uuid.uuid4().hex[:20]
        now = time.time()
        await self.db.execute(
            "INSERT INTO flash_jobs "
            "(id, aircraft_id, artifact_id, status, phase, percent, "
            " log_tail, started_at) "
            "VALUES (?, ?, ?, 'running', 'queued', 0, '', ?)",
            (job_id, aircraft_id, artifact_id, now),
        )
        self.active[aircraft_id] = job_id
        await self.hub.record_event(
            aircraft_id,
            "info",
            "flash.started",
            f"开始烧录 {artifact['filename']}",
            {"job_id": job_id, "artifact_id": artifact_id},
        )
        await self._broadcast_job(job_id)

        task = asyncio.create_task(self._run(job_id, mode))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await self.get(job_id)

    async def _run(self, job_id: str, mode: str) -> None:
        job = await self.get(job_id)
        aircraft_id = job["aircraft_id"]
        try:
            if mode == "plane":
                await self._run_plane(job)
            else:
                await self._run_ssh(job)
            await self._finish(job_id, "success", error=None)
        except ApiError as exc:
            await self._finish(job_id, "failed", error=exc.message)
        except Exception as exc:  # noqa: BLE001
            await self._finish(job_id, "failed", error=f"内部错误: {exc!r}")
        finally:
            self.active.pop(aircraft_id, None)
            await self._broadcast_job(job_id)

    async def _run_plane(self, job: dict[str, Any]) -> None:
        aircraft_id = job["aircraft_id"]
        link = self.hub.planes.get(aircraft_id)
        if link is None:
            raise ApiError(409, "plane_offline", "飞机已离线，无法继续烧录")
        artifact = await self.artifacts.get(job["artifact_id"])
        base = self.settings.public_base.rstrip("/")
        request_id = job["id"]
        fut = link.new_future(request_id)
        await link.send_json(
                {
                    "v": link.proto,
                "type": "flash_start",
                "ts": time.time(),
                "payload": {
                    "request_id": request_id,
                    "artifact_id": artifact["id"],
                    "url": f"{base}/api/artifacts/{artifact['id']}/download",
                    "sha256": artifact["sha256"],
                    "size": artifact["size"],
                    "run": True,
                },
            }
        )
        await self._update_job(
            request_id, phase="transfer", percent=1,
            message="已下发烧录请求（plane-link）",
        )
        try:
            result = await asyncio.wait_for(fut, timeout=180)
        except asyncio.TimeoutError as exc:
            link.drop(request_id)
            raise ApiError(504, "flash_timeout", "等待飞机烧录结果超时") from exc
        if not result.get("ok"):
            raise ApiError(
                502, "flash_failed", str(result.get("error") or "飞机报告烧录失败")
            )

    async def _run_ssh(self, job: dict[str, Any]) -> None:
        aircraft = await self.db.query_one(
            "SELECT * FROM aircraft WHERE id = ?", (job["aircraft_id"],)
        )
        assert aircraft is not None
        key_path = aircraft.get("ssh_key_path")
        if not key_path:
            raise ApiError(
                409,
                "ssh_key_missing",
                f"飞机 {job['aircraft_id']} 未配置 ssh.key_path",
            )
        artifact = await self.artifacts.get(job["artifact_id"])
        artifact_path = self.artifacts.path_for(artifact["id"])
        await self._update_job(job["id"], phase="connecting", percent=2)
        outcome = await asyncio.to_thread(
            self._ssh_blocking,
            aircraft,
            artifact_path,
            job["id"],
        )
        if not outcome["ok"]:
            raise ApiError(502, "flash_failed", outcome["error"] or "SSH 烧录失败")

    def _ssh_blocking(
        self,
        aircraft: dict[str, Any],
        artifact_path: Path,
        job_id: str,
    ) -> dict[str, Any]:
        lines: list[str] = []

        def report(percent: int, phase: str, line: str | None = None) -> None:
            if line:
                lines.append(line)
            self._thread_report(job_id, phase, percent, line)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        key_file = Path(aircraft["ssh_key_path"]).expanduser()
        pkey = None
        for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                pkey = cls.from_private_key_file(str(key_file))
                break
            except (paramiko.SSHException, OSError):
                continue
        if pkey is None:
            report(0, "failed", f"无法加载 SSH 私钥 {key_file}")
            return {"ok": False, "error": f"无法加载 SSH 私钥 {key_file}"}

        try:
            client.connect(
                hostname=aircraft["ssh_host"],
                username=aircraft["ssh_user"] or "root",
                pkey=pkey,
                timeout=10,
            )
            report(3, "uploading", "SSH 连接成功，正在上传固件")
            with client.open_sftp() as sftp:
                sftp.put(str(artifact_path), "/tmp/m0load_ground_upload.bin")

            report(10, "flashing", "上传完成，启动 m0load")
            _, stdout, stderr = client.exec_command(
                "m0load /tmp/m0load_ground_upload.bin --run"
            )
            stdout.channel.settimeout(180)

            tail: list[str] = []
            ok = False
            while True:
                line = stdout.readline()
                if line:
                    tail.append(line.rstrip("\n"))
                    percent, phase = self._classify_line(line, tail)
                    report(percent, phase, line.rstrip("\n"))
                    continue
                if stdout.channel.exit_status_ready():
                    break
                if stdout.channel.recv_ready():
                    continue
                try:
                    if stdout.channel.recv(1):
                        continue
                except (EOFError, OSError):
                    break

            exit_status = stdout.channel.recv_exit_status()
            for line in iter(stderr.readline, ""):
                tail.append(line.rstrip("\n"))
            if any("RESULT OK" in ln for ln in tail):
                ok = True
            report(100 if ok else 0, "started" if ok else "failed", None)
            if ok:
                return {"ok": True}
            error = (tail or ["m0load 未返回成功结果"])[-1]
            return {"ok": False, "error": f"m0load 退出码 {exit_status}: {error}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"SSH 执行失败: {exc}"}
        finally:
            client.close()

    @staticmethod
    def _classify_line(line: str, tail: list[str]) -> tuple[int, str]:
        percent = 10
        phase = "flashing"
        block = BLOCK_RE.search(line)
        if block:
            current, total = int(block.group(1)), int(block.group(2))
            if total > 0:
                percent = 10 + int(88 * current / total)
        elif "image OK" in line:
            percent = 12
            phase = "verifying"
        elif "verify OK" in line:
            percent = 98
            phase = "ready"
        elif "CMD_RUN_APP sent" in line:
            percent = 99
            phase = "launching"
        elif "RESULT OK" in line:
            percent = 100
            phase = "started"
        elif any(marker.lower() in line.lower() for marker in FAIL_MARKERS):
            phase = "failed"
        return percent, phase

    def _thread_report(
        self, job_id: str, phase: str, percent: int, line: str | None
    ) -> None:
        coro = self._report_async(job_id, phase, percent, line)
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(
            lambda f: f.exception() if not f.cancelled() and f.exception() else None
        )

    async def _report_async(
        self, job_id: str, phase: str, percent: int, line: str | None
    ) -> None:
        await self._update_job(job_id, phase=phase, percent=percent, message=line)

    async def on_plane_progress(
        self, aircraft_id: str, request_id: str, payload: dict[str, Any]
    ) -> None:
        if self.active.get(aircraft_id) != request_id:
            return
        try:
            percent = int(payload.get("percent") or 0)
        except (TypeError, ValueError):
            percent = None
        await self._update_job(
            request_id,
            phase=str(payload.get("phase") or "running"),
            percent=percent,
            message=str(payload.get("message") or "") or None,
        )

    async def _update_job(
        self,
        job_id: str,
        *,
        phase: str | None = None,
        percent: int | None = None,
        message: str | None = None,
        status: str | None = None,
    ) -> None:
        row = await self.db.query_one(
            "SELECT * FROM flash_jobs WHERE id = ?", (job_id,)
        )
        if row is None:
            return
        tail = row["log_tail"]
        if message:
            lines = tail.splitlines() if tail else []
            lines.append(message)
            if len(lines) > 80:
                lines = lines[-80:]
            tail = "\n".join(lines)
            while len(tail.encode("utf-8", "ignore")) > 16_000:
                tail = "\n".join(tail.splitlines()[1:])
        await self.db.execute(
            "UPDATE flash_jobs SET phase = ?, percent = ?, log_tail = ?, status = ? "
            "WHERE id = ?",
            (
                phase if phase is not None else row["phase"],
                percent if percent is not None else row["percent"],
                tail,
                status if status is not None else row["status"],
                job_id,
            ),
        )
        await self._broadcast_job(job_id)

    async def _finish(
        self, job_id: str, status: str, error: str | None
    ) -> None:
        await self.db.execute(
            "UPDATE flash_jobs SET status = ?, error = ?, finished_at = ? "
            "WHERE id = ?",
            (status, error, time.time(), job_id),
        )
        job = await self.get(job_id)
        await self.hub.record_event(
            job["aircraft_id"],
            "success" if status == "success" else "error",
            f"flash.{status}",
            f"烧录{'成功' if status == 'success' else '失败'}",
            {"job_id": job_id, "error": error},
        )
        await self._broadcast_job(job_id)

    async def _broadcast_job(self, job_id: str) -> None:
        try:
            job = await self.get(job_id)
        except ApiError:
            return
        await self.hub.broadcast_ui(
            {"type": "flash_job", "payload": {"job": job}}
        )
