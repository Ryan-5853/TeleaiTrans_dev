import struct
import zlib

from dataclasses import dataclass

M0AP_MAGIC = 0x4D304150
MAX_APP_IMAGE_SIZE = 0x7400
APP_BASE = 0x00000800
HEADER_SIZE = 24


class M0ApError(ValueError):
    pass


@dataclass(frozen=True)
class M0ApInfo:
    version: int
    image_size: int
    load_addr: int
    entry: int
    crc32: int
    sha256: str


def parse_m0ap(data: bytes, sha256: str | None = None) -> M0ApInfo:
    if len(data) < HEADER_SIZE:
        raise M0ApError(f"too small for M0AP header ({len(data)} bytes)")

    magic, version, image_size, load_addr, entry, crc32 = struct.unpack_from(
        "<IIIIII", data, 0
    )
    if magic != M0AP_MAGIC:
        raise M0ApError(f"bad magic 0x{magic:08X}, expected 0x{M0AP_MAGIC:08X}")
    if image_size != len(data):
        raise M0ApError(f"header size {image_size} != file size {len(data)}")
    if len(data) % 4 != 0:
        raise M0ApError("image size not 4-byte aligned")
    if not (0 < image_size <= MAX_APP_IMAGE_SIZE):
        raise M0ApError(f"image size {image_size} out of range (1..{MAX_APP_IMAGE_SIZE})")
    if load_addr != APP_BASE:
        raise M0ApError(f"bad load address 0x{load_addr:08X}, expected 0x{APP_BASE:08X}")
    if not (entry & 1) or not (APP_BASE + HEADER_SIZE <= entry < MAX_APP_IMAGE_SIZE):
        raise M0ApError(f"bad Thumb entry 0x{entry:08X}")

    patched = bytearray(data)
    struct.pack_into("<I", patched, 20, 0)
    actual_crc = zlib.crc32(bytes(patched)) & 0xFFFFFFFF
    if actual_crc != crc32:
        raise M0ApError(f"CRC mismatch: header 0x{crc32:08X} != computed 0x{actual_crc:08X}")

    if sha256 is None:
        import hashlib

        sha256 = hashlib.sha256(data).hexdigest()

    return M0ApInfo(
        version=version,
        image_size=image_size,
        load_addr=load_addr,
        entry=entry,
        crc32=crc32,
        sha256=sha256,
    )
