#!/usr/bin/env python3
import base64
import binascii
import re
import struct
from pathlib import Path

SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")

# base64 substrings that likely contain the string "secret" => b"c2VjcmV0"
B64_SECRET_MARK = b"c2VjcmV0"
B64_CAND_RE = re.compile(rb"[A-Za-z0-9+/]{24,}={0,2}")


def parse_png_chunks(data: bytes):
    pos = 8
    chunks = []
    while pos + 12 <= len(data):
        ln = struct.unpack(">I", data[pos:pos+4])[0]
        typ = data[pos+4:pos+8]
        cdata = data[pos+8:pos+8+ln]
        chunks.append((typ, cdata))
        pos += 12 + ln
        if typ == b"IEND":
            break
    return chunks


def decode_base64_blob(s: bytes):
    # Pad to 4-byte boundary.
    pad = (-len(s)) % 4
    if pad:
        s2 = s + (b"=" * pad)
    else:
        s2 = s
    return base64.b64decode(s2, validate=False)


def scan_blob(blob: bytes):
    found = set(SECRET_RE.findall(blob))

    # base64 candidates with secret marker
    for m in B64_CAND_RE.finditer(blob):
        cand = m.group(0)
        if B64_SECRET_MARK not in cand:
            continue
        try:
            dec = decode_base64_blob(cand)
            found |= set(SECRET_RE.findall(dec))
            # also attempt zlib decompress
            try:
                import zlib
                found |= set(SECRET_RE.findall(zlib.decompress(dec)))
            except Exception:
                pass
        except Exception:
            pass

    return {f.decode('ascii', 'ignore').lower() for f in found}


def main():
    for img in sorted(Path('.').glob('puzzle_0009.png')) + sorted(Path('.').glob('puzzle_0010.png')):
        pass

    targets = [
        'puzzle_0009.png','puzzle_0010.png','puzzle_0011.png','puzzle_0012.png'
    ]
    for t in targets:
        data = Path(t).read_bytes()
        chunks = parse_png_chunks(data)
        out = {}
        for typ, cdata in chunks:
            secrets = scan_blob(cdata)
            if secrets:
                out[typ.decode('latin1','replace')] = sorted(secrets)
        if out:
            print(t)
            for typ in sorted(out):
                for s in out[typ]:
                    print(' ',typ, s)
        else:
            print(t, '<NOT_FOUND>')

if __name__ == '__main__':
    main()
