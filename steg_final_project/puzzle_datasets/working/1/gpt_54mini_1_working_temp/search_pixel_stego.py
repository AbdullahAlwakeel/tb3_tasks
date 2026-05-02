#!/usr/bin/env python3
import base64
import itertools
import os
import re
import struct
import zlib
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"
SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def read_png(path):
    data = Path(path).read_bytes()
    if not data.startswith(PNG_SIG):
        raise ValueError("not png")
    pos = len(PNG_SIG)
    width = height = bitdepth = colortype = None
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos+4])[0]
        ctype = data[pos+4:pos+8]
        chunk = data[pos+8:pos+8+length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bitdepth, colortype, comp, filt, inter = struct.unpack(">IIBBBBB", chunk)
            if bitdepth != 8 or inter != 0:
                raise ValueError(f"unsupported png params bd={bitdepth} inter={inter}")
        elif ctype == b"IDAT":
            idat.extend(chunk)
        elif ctype == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    return width, height, colortype, raw


def unfilter(width, height, color_type, raw):
    channels = {2: 3, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError(f"unsupported color type {color_type}")
    stride = width * channels
    out = bytearray(height * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        f = raw[p]
        p += 1
        scan = bytearray(raw[p:p+stride])
        p += stride
        if f == 0:
            recon = scan
        elif f == 1:
            recon = bytearray(stride)
            for i in range(stride):
                left = recon[i-channels] if i >= channels else 0
                recon[i] = (scan[i] + left) & 0xff
        elif f == 2:
            recon = bytearray((scan[i] + prev[i]) & 0xff for i in range(stride))
        elif f == 3:
            recon = bytearray(stride)
            for i in range(stride):
                left = recon[i-channels] if i >= channels else 0
                up = prev[i]
                recon[i] = (scan[i] + ((left + up) >> 1)) & 0xff
        elif f == 4:
            recon = bytearray(stride)
            for i in range(stride):
                a = recon[i-channels] if i >= channels else 0
                b = prev[i]
                c = prev[i-channels] if i >= channels else 0
                p_ = a + b - c
                pa = abs(p_ - a)
                pb = abs(p_ - b)
                pc = abs(p_ - c)
                pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                recon[i] = (scan[i] + pr) & 0xff
        else:
            raise ValueError(f"bad filter {f}")
        out[y*stride:(y+1)*stride] = recon
        prev = recon
    return bytes(out), channels


def bits_to_bytes(bits, msb_first=False):
    out = bytearray()
    cur = 0
    n = 0
    for b in bits:
        if msb_first:
            cur = (cur << 1) | b
        else:
            cur |= (b << n)
        n += 1
        if n == 8:
            out.append(cur & 0xff)
            cur = 0
            n = 0
    return bytes(out)


def try_deep_decode(blob):
    # search blob directly
    m = SECRET_RE.search(blob)
    if m:
        return m.group(0).decode()
    # if printable and base64-ish, try decoding
    txt = blob.strip()
    if len(txt) >= 16:
        printable = sum(32 <= b < 127 for b in txt) / len(txt)
        if printable > 0.9 and all((65 <= b <= 90) or (97 <= b <= 122) or (48 <= b <= 57) or b in b'+/=\n\r' for b in txt):
            try:
                dec = base64.b64decode(txt, validate=False)
                m = SECRET_RE.search(dec)
                if m:
                    return m.group(0).decode()
            except Exception:
                pass
    return None


def search_file(path):
    width, height, ctype, raw = read_png(path)
    pixels, channels = unfilter(width, height, ctype, raw)
    groups = []
    idxs = list(range(channels))
    for r in range(1, channels + 1):
        # all permutations of length r, but prefer full channel set and singles
        if r not in (1, channels):
            continue
        for perm in itertools.permutations(idxs, r):
            groups.append(perm)
    seen = set()
    for perm in groups:
        key = (perm,)
        if key in seen:
            continue
        seen.add(key)
        # collect sample values in chosen channel order
        vals = []
        for i in range(width * height):
            base = i * channels
            for ch in perm:
                vals.append(pixels[base + ch])
        # direct byte search on raw values
        hit = try_deep_decode(bytes(vals))
        if hit:
            return ("raw", perm, None, None, hit)
        for bit in range(8):
            bits = [((v >> bit) & 1) for v in vals]
            for msb_first in (False, True):
                payload = bits_to_bytes(bits, msb_first=msb_first)
                hit = try_deep_decode(payload)
                if hit:
                    return ("bit", perm, bit, msb_first, hit)
    return None


def main():
    files = [Path(p) for p in os.listdir(".") if p.endswith(".png")]
    for f in sorted(files):
        try:
            res = search_file(f)
        except Exception as e:
            print(f"{f}: ERROR {e}")
            continue
        if res:
            kind, perm, bit, msb_first, hit = res
            print(f"{f}: {hit} via {kind} perm={perm} bit={bit} msb_first={msb_first}")


if __name__ == "__main__":
    main()
