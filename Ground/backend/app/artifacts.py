import asyncio
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .db import Database
from .errors import ApiError
from .m0ap import M0ApError, parse_m0ap


class ArtifactService:
    def __init__(self, db: Database, artifact_dir: str):
        self.db = db
        self.dir = Path(artifact_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, artifact_id: str) -> Path:
        return self.dir / f"{artifact_id}.bin"

    @staticmethod
    def view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "filename": row["filename"],
            "size": row["size"],
            "sha256": row["sha256"],
            "version": row["version"],
            "load_addr": row["load_addr"],
            "entry": row["entry"],
            "crc32": row["crc32"],
            "uploaded_at": row["uploaded_at"],
        }

    async def store(self, filename: str, data: bytes) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        existing = await self.db.query_one(
            "SELECT * FROM artifacts WHERE id = ?", (digest,)
        )
        if existing is not None:
            return self.view(existing)

        try:
            info = parse_m0ap(data, digest)
        except M0ApError as exc:
            raise ApiError(422, "bad_artifact", f"M0AP 校验失败: {exc}") from exc

        def _write() -> None:
            target = self.path_for(digest)
            fd, tmp = tempfile.mkstemp(prefix=".upload-", dir=self.dir)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

        await asyncio.to_thread(_write)
        now = time.time()
        await self.db.execute(
            "INSERT INTO artifacts "
            "(id, filename, size, sha256, version, load_addr, entry, crc32, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                digest,
                filename,
                len(data),
                digest,
                info.version,
                info.load_addr,
                info.entry,
                info.crc32,
                now,
            ),
        )
        row = await self.db.query_one(
            "SELECT * FROM artifacts WHERE id = ?", (digest,)
        )
        assert row is not None
        return self.view(row)

    async def list(self) -> list[dict[str, Any]]:
        rows = await self.db.query_all(
            "SELECT * FROM artifacts ORDER BY uploaded_at DESC, id"
        )
        return [self.view(row) for row in rows]

    async def get(self, artifact_id: str) -> dict[str, Any]:
        row = await self.db.query_one(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        )
        if row is None:
            raise ApiError(404, "artifact_not_found", f"固件产物 {artifact_id} 不存在")
        return self.view(row)

    async def read_bytes(self, artifact_id: str) -> bytes:
        await self.get(artifact_id)
        path = self.path_for(artifact_id)
        if not path.is_file():
            raise ApiError(410, "artifact_file_missing", "固件文件已丢失")
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, artifact_id: str) -> None:
        await self.get(artifact_id)
        await self.db.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
        path = self.path_for(artifact_id)
        await asyncio.to_thread(path.unlink, missing_ok=True)
