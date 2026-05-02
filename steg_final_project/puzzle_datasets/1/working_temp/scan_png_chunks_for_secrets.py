import glob
import os
import re
import struct
import zlib


SECRET_RE = re.compile(br"secret\{[0-9a-fA-F]{8}\}")


def read_chunks(path: str):
    with open(path, "rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not PNG")
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                return
            length, typ = struct.unpack("!I4s", hdr)
            data = f.read(length)
            f.read(4)  # crc
            yield typ, data
            if typ == b"IEND":
                return


def find_in_bytes(b: bytes):
    m = SECRET_RE.search(b)
    return m.group(0) if m else None


def decode_zTXt(data: bytes):
    # keyword\0 compression_method(1 byte) compressed_text...
    try:
        nul = data.index(b"\x00")
    except ValueError:
        return None
    keyword = data[:nul]
    rest = data[nul + 1 :]
    if not rest:
        return None
    cm = rest[0]
    if cm != 0:
        return None
    comp = rest[1:]
    try:
        return zlib.decompress(comp)
    except Exception:
        return None


def decode_iTXt(data: bytes):
    # keyword\0 compression_flag\0 compression_method\0 translated_keyword\0 text...
    # The PNG spec is involved; implement a best-effort parser.
    try:
        p = 0
        nul1 = data.index(b"\x00", p)
        keyword = data[:nul1]
        p = nul1 + 1
        if p >= len(data):
            return None
        compression_flag = data[p]
        p += 1
        nul2 = data.index(b"\x00", p)
        compression_method = data[p:nul2]
        p = nul2 + 1
        nul3 = data.index(b"\x00", p)
        _translated_keyword = data[p:nul3]
        p = nul3 + 1
        text = data[p:]
        if compression_flag == 0:
            return text
        if compression_flag == 1:
            # compressed using the compression method indicated (usually 0 = deflate)
            try:
                return zlib.decompress(text)
            except Exception:
                return None
        return None
    except Exception:
        return None


def main():
    root = sys.argv[1] if len(sys.argv) >= 2 else "."
    pngs = sorted([p for p in glob.glob(os.path.join(root, "puzzle_*.png"))])
    for p in pngs:
        found = None
        for typ, data in read_chunks(p):
            m = find_in_bytes(data)
            if m:
                found = m
                break

            if typ == b"zTXt":
                dec = decode_zTXt(data)
                if dec:
                    m = find_in_bytes(dec)
                    if m:
                        found = m
                        break

            if typ == b"iTXt":
                dec = decode_iTXt(data)
                if dec:
                    m = find_in_bytes(dec)
                    if m:
                        found = m
                        break

        name = os.path.basename(p)
        if found:
            print(f"{name}: {found.decode('ascii', errors='replace')}")
        else:
            print(f"{name}: NOT_FOUND")


if __name__ == "__main__":
    import sys
    raise SystemExit(main())

