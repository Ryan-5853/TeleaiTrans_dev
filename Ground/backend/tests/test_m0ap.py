import struct
import zlib

import pytest

from app.m0ap import M0AP_MAGIC, M0ApError, parse_m0ap


def make_m0ap_image(size: int = 512) -> bytes:
    payload = bytes((i * 7) & 0xFF for i in range(max(0, size - 24)))
    header = struct.pack(
        "<IIIIII", M0AP_MAGIC, 1, size, 0x800, 0x819, 0
    )
    data = bytearray(header + payload)
    struct.pack_into("<I", data, 20, zlib.crc32(bytes(data)) & 0xFFFFFFFF)
    return bytes(data)


def test_parse_valid_image():
    data = make_m0ap_image(512)
    info = parse_m0ap(data)
    assert info.image_size == 512
    assert info.load_addr == 0x800
    assert info.entry == 0x819


def test_rejects_bad_magic():
    data = bytearray(make_m0ap_image(512))
    struct.pack_into("<I", data, 0, 0xDEADBEEF)
    with pytest.raises(M0ApError, match="magic"):
        parse_m0ap(bytes(data))


def test_rejects_size_mismatch_and_alignment():
    data = make_m0ap_image(512)
    with pytest.raises(M0ApError, match="header size"):
        parse_m0ap(data + b"\x00")
    with pytest.raises(M0ApError, match="too small"):
        parse_m0ap(b"\x00" * 20)


def test_rejects_corrupt_crc():
    data = bytearray(make_m0ap_image(512))
    data[40] ^= 0xFF
    with pytest.raises(M0ApError, match="CRC mismatch"):
        parse_m0ap(bytes(data))
