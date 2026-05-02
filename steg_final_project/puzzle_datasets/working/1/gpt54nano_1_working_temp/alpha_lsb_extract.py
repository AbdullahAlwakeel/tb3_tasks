#!/usr/bin/env python3
import re
import struct
import base64
import binascii
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

SECRET_RE = re.compile(r"secret\{([0-9a-fA-F]{8})\}")
B64_RE = re.compile(rb"^[A-Za-z0-9+/=\r\n\t ]+$")


def find_secret_bytes(blob: bytes):
    # returns set of normalized secrets (lowercase)
    out = set()
    try:
        for m in SECRET_RE.finditer(blob.decode('latin1', errors='ignore')):
            out.add('secret{' + m.group(1).lower() + '}')
    except Exception:
        pass
    return out


def try_payload(payload: bytes):
    found = set()
    found |= find_secret_bytes(payload)

    # Try base64 decode if content looks like base64.
    stripped = re.sub(rb"\s+", b"", payload)
    if len(stripped) >= 12 and B64_RE.match(payload):
        # may have padding issues
        pad_len = (-len(stripped)) % 4
        try:
            dec = base64.b64decode(stripped + b"=" * pad_len, validate=False)
            found |= find_secret_bytes(dec)
            try:
                found |= find_secret_bytes(zlib.decompress(dec))
            except Exception:
                pass
        except Exception:
            pass

    # Try zlib directly.
    try:
        found |= find_secret_bytes(zlib.decompress(payload))
    except Exception:
        pass

    return found


def extract_alpha_payloads(img_path: str, bits=(0,1,2,3,4,5,6,7), max_payload=2_000_000):
    im = Image.open(img_path).convert('RGBA')
    arr = np.array(im, dtype=np.uint8)
    alpha = arr[:, :, 3].reshape(-1)

    results = set()
    for bit in bits:
        bitstream = (alpha >> bit) & 1
        bitstream = bitstream.astype(np.uint8)
        nbits = (bitstream.size // 8) * 8
        if nbits < 8:
            continue
        bitstream = bitstream[:nbits]
        bitstream2d = bitstream.reshape(-1, 8)

        for bitorder in ('big', 'little'):
            data = np.packbits(bitstream2d, axis=1, bitorder=bitorder).reshape(-1).tobytes()
            if len(data) < 4:
                continue
            # Try both endian for length.
            for endian in ('>', '<'):
                n = struct.unpack(endian + 'I', data[:4])[0]
                if n <= 0 or n > max_payload:
                    continue
                if 4 + n > len(data):
                    continue
                payload = data[4:4+n]
                found = try_payload(payload)
                if found:
                    results |= found
    return results


def main():
    imgs = sorted(Path('.').glob('puzzle_*.png'))
    for img in imgs:
        found = extract_alpha_payloads(str(img))
        if found:
            for s in sorted(found):
                print(f"{img.name}: {s}")
        else:
            print(f"{img.name}: <NOT_FOUND>")


if __name__ == '__main__':
    main()
