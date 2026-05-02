#!/usr/bin/env python3
import base64
import binascii
import io
import os
import struct
import sys
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def parse_png(path):
    with open(path, "rb") as f:
        data = f.read()

    if not data.startswith(PNG_SIG):
        return {"error": "not png"}

    pos = len(PNG_SIG)
    chunks = []
    trailer = b""
    while pos + 8 <= len(data):
        if pos + 8 > len(data):
            break
        length = struct.unpack(">I", data[pos:pos+4])[0]
        ctype = data[pos+4:pos+8].decode("latin1", errors="replace")
        start = pos + 8
        end = start + length
        crc_end = end + 4
        if crc_end > len(data):
            break
        chunk_data = data[start:end]
        chunks.append((ctype, length, chunk_data))
        pos = crc_end
        if ctype == "IEND":
            trailer = data[pos:]
            break
    return {"chunks": chunks, "trailer": trailer, "size": len(data)}


def printable_preview(b, limit=120):
    s = b.decode("utf-8", errors="replace")
    s = s.replace("\x00", "\\x00")
    return s[:limit] + ("..." if len(s) > limit else "")


def try_decompress(data):
    for wbits in (15, -15, 31):
        try:
            return zlib.decompress(data, wbits)
        except Exception:
            pass
    return None


def main():
    files = sorted([f for f in os.listdir(".") if f.endswith(".png")])
    for path in files:
        info = parse_png(path)
        print(f"## {path}")
        if "error" in info:
            print(info["error"])
            continue
        print(f"size={info['size']} trailer={len(info['trailer'])}")
        for ctype, length, chunk_data in info["chunks"]:
            if ctype in ("tEXt", "zTXt", "iTXt", "eXIf", "iCCP", "IDAT", "IHDR", "IEND", "PLTE", "tRNS"):
                note = ""
                if ctype == "tEXt":
                    note = printable_preview(chunk_data)
                elif ctype == "zTXt":
                    note = printable_preview(chunk_data[:80])
                    parts = chunk_data.split(b"\x00", 2)
                    if len(parts) >= 3:
                        dec = try_decompress(parts[2][1:])
                        if dec:
                            note += " | " + printable_preview(dec, 200)
                elif ctype == "iTXt":
                    note = printable_preview(chunk_data, 180)
                elif ctype == "iCCP":
                    parts = chunk_data.split(b"\x00", 2)
                    if len(parts) >= 3:
                        dec = try_decompress(parts[2][1:])
                        note = f"icc_name={parts[0].decode('latin1', 'replace')} dec_len={len(dec) if dec else 'none'}"
                elif ctype == "eXIf":
                    note = f"exif_bytes={length}"
                elif ctype == "IHDR":
                    w, h, bd, ct, comp, flt, inter = struct.unpack(">IIBBBBB", chunk_data)
                    note = f"{w}x{h} bd={bd} ct={ct} inter={inter}"
                elif ctype == "IEND" and info["trailer"]:
                    note = f"trailer_preview={printable_preview(info['trailer'], 80)}"
                print(f"  {ctype} len={length} {note}")
        if info["trailer"]:
            print(f"  TRAILER_RAW {info['trailer'][:32].hex()}")


if __name__ == "__main__":
    main()
