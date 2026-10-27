#!/usr/bin/env python3
"""Generate a small SYNTHETIC eval set (no real camera images).

Writes examples/synthetic-eval/{sample.json,truth.json,snaps/*.png}:
12 solid-colour images whose truth labels are assigned by the generator.
The point is a smoke-test corpus a fresh checkout can eval end-to-end against
a real Ollama - accuracy here says nothing about real-world performance.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "examples" / "synthetic-eval"
LABELS = ["person_property", "vehicle_street", "person_property", "nothing",
          "vehicle_street", "animal", "person_street", "person_property",
          "nothing", "vehicle_driveway", "person_property", "vehicle_street"]
COLOURS = [(200, 40, 40), (40, 60, 200), (200, 40, 40), (90, 90, 90),
           (40, 60, 200), (220, 180, 30), (60, 160, 60), (200, 40, 40),
           (90, 90, 90), (30, 160, 160), (200, 40, 40), (40, 60, 200)]


def png_bytes(red: int, green: int, blue: int, size: int = 8) -> bytes:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes((red, green, blue)) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def main() -> None:
    snaps = HERE / "snaps"
    snaps.mkdir(parents=True, exist_ok=True)
    sample = []
    for i, label in enumerate(LABELS):
        event_id = f"synthetic-{i:03d}"
        (snaps / f"{event_id}.png").write_bytes(png_bytes(*COLOURS[i]))
        sample.append({"id": event_id, "camera": "synthetic",
                       "label": label.split("_")[0], "score": 0.8, "zones": []})
    (HERE / "sample.json").write_text(json.dumps(sample, indent=1))
    (HERE / "truth.json").write_text(
        json.dumps({"scene": LABELS,
                    "notes": "SYNTHETIC smoke-test corpus - colours only, no real cameras."}, indent=1))
    print(f"wrote {len(LABELS)} synthetic images to {HERE}")


if __name__ == "__main__":
    main()
