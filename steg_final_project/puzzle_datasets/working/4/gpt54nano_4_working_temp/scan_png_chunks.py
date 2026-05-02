import os
import re
import json
import glob
import zlib
from typing import Optional, List, Tuple


SECRET_RE_BYTES = re.compile(rb"secret\{[0-9a-fA-F]{8}\}", re.IGNORECASE)


def find_secret_in_bytes(data: bytes) -> Optional[str]:
    m = SECRET_RE_BYTES.search(data)
    if not m:
        return None
    s = m.group(0).decode("ascii", errors="ignore")
    return s


def iter_png_chunks(buf: bytes):
    # PNG: 8-byte signature, then repeated:
    # length (4 bytes big-endian), type (4 bytes), data (length), crc (4 bytes)
    if buf[:8] != b"\x89PNG\r\n\x1a\n":
        return
    i = 8
    n = len(buf)
    while i + 8 <= n:
        length = int.from_bytes(buf[i : i + 4], "big")
        ctype = buf[i + 4 : i + 8]
        i += 8
        if i + length + 4 > n:
            break
        data = buf[i : i + length]
        i += length
        # crc = buf[i : i + 4]
        i += 4
        yield ctype, data


def scan_png_for_secret_chunks(path: str) -> Optional[str]:
    with open(path, "rb") as f:
        buf = f.read()

    for ctype, data in iter_png_chunks(buf):
        # tEXt: keyword\0text
        if ctype == b"tEXt":
            found = find_secret_in_bytes(data)
            if found:
                return found

        # zTXt: keyword\0compression_method(1)\0compressed_text(zlib)
        elif ctype == b"zTXt":
            try:
                # Split at first null after keyword: keyword\0method
                # data = keyword\0compression_method + compressed_text
                parts = data.split(b"\x00", 2)
                if len(parts) != 3:
                    continue
                keyword, method_and_rest = parts[0], parts[1:]
                # method_and_rest[0] should be method (1 byte)
                # But because split(b"\x00",2) behaves differently, do it robustly:
                # keyword\0method(1 byte)compressed_data
                # Find keyword terminator:
                null_pos = data.find(b"\x00")
                if null_pos < 0 or null_pos + 2 > len(data):
                    continue
                method = data[null_pos + 1]
                if method != 0:
                    continue
                compressed = data[null_pos + 2 :]
                decompressed = zlib.decompress(compressed)
                found = find_secret_in_bytes(decompressed)
                if found:
                    return found
            except Exception:
                continue

        # iTXt: keyword\0compression_flag(1)\0compression_method(?? in spec) ...
        elif ctype == b"iTXt":
            try:
                # Format: keyword\0 compression_flag (1 byte) \0 compression_method (1 byte) ...
                # We'll parse loosely by splitting on NULs.
                parts = data.split(b"\x00")
                if len(parts) < 5:
                    continue
                keyword = parts[0]
                compression_flag = parts[1]
                # If compression_flag is b'0', plain; if b'1' then following is compressed.
                if not compression_flag:
                    continue
                if compression_flag[0:1] == b"\x00":
                    # Might be b'\x00' due to our split; treat as uncompressed.
                    compression_flag_value = 0
                else:
                    compression_flag_value = compression_flag[0] - 48 if compression_flag[:1] in (b"0", b"1") else compression_flag[0]
                if compression_flag_value not in (0, 1):
                    # Fallback: interpret literal '0'/'1' if present.
                    compression_flag_value = 1 if compression_flag[:1] == b"1" else 0

                if compression_flag_value == 0:
                    found = find_secret_in_bytes(data)
                    if found:
                        return found
                else:
                    # Try: compression_method and then language tag etc exist; compressed text begins after 2nd 0 byte
                    # We'll locate compressed text by searching for first occurrence of compression_method byte after flags.
                    # Simpler: attempt to decompress any trailing zlib stream.
                    # iTXt text for compressed flag uses zlib from some offset; try common offsets:
                    for start in range(0, min(200, len(data) - 1)):
                        if start < len(data) and data[start : start + 2] == b"\x78\x9c":
                            try:
                                decompressed = zlib.decompress(data[start:])
                                found = find_secret_in_bytes(decompressed)
                                if found:
                                    return found
                            except Exception:
                                pass
            except Exception:
                continue

    return None


def main() -> None:
    os.makedirs("working_temp", exist_ok=True)
    missing: List[str] = []
    try:
        with open("working_temp/secrets.json", "r") as f:
            items = json.load(f)
        for it in items:
            if not it.get("secret"):
                missing.append(it["path"])
    except Exception:
        missing = [os.path.basename(p) for p in glob.glob("puzzle_*.png")]

    out = {}
    for rel in sorted(missing):
        path = rel if rel.startswith("puzzle_") else rel
        if not os.path.exists(path):
            # secrets.json stores just puzzle_XXXX.png filenames; cwd already.
            path = os.path.join(os.getcwd(), os.path.basename(rel))
        found = scan_png_for_secret_chunks(path)
        out[os.path.basename(path)] = found
        print(f"{os.path.basename(path)}: {found or 'NOT_FOUND'}")

    with open("working_temp/png_chunk_secrets.json", "w") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()

