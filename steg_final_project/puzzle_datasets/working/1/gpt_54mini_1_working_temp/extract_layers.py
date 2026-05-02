#!/usr/bin/env python3
import base64
import os
import struct
import subprocess
from pathlib import Path


def get_field(file, field):
    try:
        out = subprocess.check_output(
            ["exiftool", "-s3", f"-{field}", file],
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
    except subprocess.CalledProcessError:
        return ""
    return out[0].strip() if out else ""


def maybe_strip_length(blob):
    if len(blob) >= 4:
        n = struct.unpack(">I", blob[:4])[0]
        if n == len(blob) - 4:
            return blob[4:]
    return blob


def decode_base64_text(s):
    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        return None


def main():
    outdir = Path("working_temp/extracted")
    outdir.mkdir(parents=True, exist_ok=True)
    for f in sorted(p for p in os.listdir(".") if p.endswith(".png")):
        # trailer bytes after IEND
        data = Path(f).read_bytes()
        sig = b"\x89PNG\r\n\x1a\n"
        pos = len(sig)
        trailer = b""
        while pos + 8 <= len(data):
            n = struct.unpack(">I", data[pos:pos+4])[0]
            t = data[pos+4:pos+8]
            pos += 12 + n
            if t == b"IEND":
                trailer = data[pos:]
                break
        if trailer:
            raw = maybe_strip_length(trailer)
            (outdir / f"{f}.trailer.bin").write_bytes(raw)
            try:
                txt = raw.decode()
                print(f"{f} trailer text: {txt[:120]}")
                dec = decode_base64_text(txt)
                if dec is not None:
                    dec2 = maybe_strip_length(dec)
                    (outdir / f"{f}.trailer.dec").write_bytes(dec2)
                    print(f"  base64-> {len(dec2)} bytes head={dec2[:16].hex()}")
            except Exception:
                print(f"{f} trailer raw {len(raw)} bytes")

        desc = get_field(f, "ImageDescription")
        if desc:
            raw = maybe_strip_length(desc.encode())
            (outdir / f"{f}.ImageDescription.txt").write_bytes(raw)
            print(f"{f} desc head: {raw[:120]!r}")
            dec = decode_base64_text(raw.decode(errors="ignore"))
            if dec is not None:
                dec2 = maybe_strip_length(dec)
                (outdir / f"{f}.ImageDescription.dec").write_bytes(dec2)
                print(f"  base64-> {len(dec2)} bytes head={dec2[:16].hex()}")


if __name__ == "__main__":
    main()
