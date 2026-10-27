"""Shared test helpers. All images here are SYNTHETIC - generated PNGs, never
real camera snapshots."""
import struct
import zlib

from frigate_decide.decide import Decision


def png_bytes(red: int = 200, green: int = 30, blue: int = 30, size: int = 8) -> bytes:
    """A tiny valid PNG of one solid colour - enough to exercise the pipeline."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes((red, green, blue)) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_decision(answers: dict | None = None, gate: str = "decided") -> Decision:
    return Decision(
        answers=answers if answers is not None else {},
        latency_ms=5,
        endpoint=None if gate == "fail_open" else "http://fake:11434",
        model="d1",
        gate=gate,
    )


FRIGATE_EVENT = {
    "before": None,
    "after": {
        "id": "1793046391.796194-xbchwp",
        "camera": "front_cam",
        "label": "person",
        "score": 0.78,
        "current_zones": ["yard"],
        "stationary": False,
        "end_time": None,
        "snapshot": {"score": 0.78},
    },
}
