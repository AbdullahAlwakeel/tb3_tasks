#!/usr/bin/env python3
import base64
import binascii
import glob
import os
import re
import struct
import subprocess
import zlib

SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "working_temp")


def find_secrets(data):
    return sorted(set(m.group(0).decode("ascii") for m in SECRET_RE.finditer(data)))


def parse_png(path):
    data = open(path, "rb").read()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not png")
    pos = 8
    chunks = []
    while pos + 12 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        cdata = data[pos + 8:pos + 8 + ln]
        crc = data[pos + 8 + ln:pos + 12 + ln]
        chunks.append((typ, cdata, pos, ln))
        pos += 12 + ln
        if typ == b"IEND":
            break
    return data, chunks, data[pos:]


def png_pixels(chunks):
    width = height = bitdepth = colortype = None
    idat = []
    for typ, cdata, _, _ in chunks:
        if typ == b"IHDR":
            width, height, bitdepth, colortype, comp, filt, inter = struct.unpack(">IIBBBBB", cdata)
            if bitdepth != 8 or inter != 0:
                raise ValueError("unsupported png")
        elif typ == b"IDAT":
            idat.append(cdata)
    channels = {2: 3, 6: 4}[colortype]
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    rows = []
    prev = bytearray(stride)
    off = 0
    for _ in range(height):
        f = raw[off]
        off += 1
        scan = bytearray(raw[off:off + stride])
        off += stride
        recon = bytearray(stride)
        bpp = channels
        for i, x in enumerate(scan):
            a = recon[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 0:
                val = x
            elif f == 1:
                val = x + a
            elif f == 2:
                val = x + b
            elif f == 3:
                val = x + ((a + b) >> 1)
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                val = x + pr
            else:
                raise ValueError("bad filter")
            recon[i] = val & 255
        rows.append(bytes(recon))
        prev = recon
    return width, height, channels, b"".join(rows)


def bits_to_bytes(bits, msb=True):
    out = bytearray()
    n = len(bits) - (len(bits) % 8)
    for i in range(0, n, 8):
        v = 0
        if msb:
            for b in bits[i:i + 8]:
                v = (v << 1) | b
        else:
            for j, b in enumerate(bits[i:i + 8]):
                v |= b << j
        out.append(v)
    return bytes(out)


def scan_bitstreams(name, w, h, ch, pix):
    found = []
    channel_sets = []
    labels = ["r", "g", "b", "a"][:ch]
    for ci, lab in enumerate(labels):
        channel_sets.append((lab, [ci]))
    channel_sets.extend([("rgb", list(range(min(3, ch)))), ("all", list(range(ch)))])
    orders = [
        ("row", range(h), range(w)),
        ("row_rev", range(h), range(w - 1, -1, -1)),
        ("col", range(w), range(h)),
        ("col_rev", range(w), range(h - 1, -1, -1)),
    ]
    for bit in range(8):
        for cname, cis in channel_sets:
            for oname, outer, inner in orders:
                bits = []
                if oname.startswith("row"):
                    for y in outer:
                        rowoff = y * w * ch
                        for x in inner:
                            base = rowoff + x * ch
                            for ci in cis:
                                bits.append((pix[base + ci] >> bit) & 1)
                else:
                    for x in outer:
                        for y in inner:
                            base = (y * w + x) * ch
                            for ci in cis:
                                bits.append((pix[base + ci] >> bit) & 1)
                for endian in (True, False):
                    by = bits_to_bytes(bits, endian)
                    secrets = find_secrets(by)
                    if secrets:
                        found.append((f"bit{bit}:{cname}:{oname}:{'msb' if endian else 'lsb'}", secrets))
    return found


def try_text_decodings(label, data):
    hits = []
    for desc, blob in [(label, data)]:
        hits += [(desc, s) for s in find_secrets(blob)]
        stripped = re.sub(rb"\s+", b"", blob)
        for enc in ("b64", "b32", "hex"):
            try:
                if enc == "b64":
                    dec = base64.b64decode(stripped + b"=" * ((4 - len(stripped) % 4) % 4), validate=False)
                elif enc == "b32":
                    dec = base64.b32decode(stripped + b"=" * ((8 - len(stripped) % 8) % 8), casefold=True)
                else:
                    if len(stripped) % 2:
                        continue
                    dec = binascii.unhexlify(stripped)
            except Exception:
                continue
            hits += [(desc + ":" + enc, s) for s in find_secrets(dec)]
            if enc in ("b64", "b32"):
                try:
                    dec2 = zlib.decompress(dec)
                    hits += [(desc + ":" + enc + ":zlib", s) for s in find_secrets(dec2)]
                except Exception:
                    pass
    return hits


def main():
    report = []
    for path in sorted(glob.glob(os.path.join(ROOT, "puzzle_*.png"))):
        name = os.path.basename(path)
        data, chunks, trailer = parse_png(path)
        report.append(f"== {name} ==")
        report.append("chunks " + " ".join(f"{t.decode('latin1')}:{ln}" for t, _, _, ln in chunks))
        for s in find_secrets(data):
            report.append(f"raw {s}")
        if trailer:
            outp = os.path.join(OUT, f"{name}.trailer.bin")
            open(outp, "wb").write(trailer)
            report.append(f"trailer {len(trailer)} bytes -> {os.path.relpath(outp, ROOT)}")
            for where, s in try_text_decodings("trailer", trailer):
                report.append(f"{where} {s}")
        for typ, cdata, _, ln in chunks:
            if typ not in (b"IHDR", b"IDAT", b"IEND", b"iCCP", b"eXIf", b"pHYs", b"sRGB", b"gAMA", b"cHRM"):
                report.append(f"ancillary {typ.decode('latin1')} {ln}")
            if typ in (b"tEXt", b"zTXt", b"iTXt") or ln < 200000:
                for where, s in try_text_decodings(typ.decode("latin1"), cdata):
                    report.append(f"{where} {s}")
        try:
            w, h, ch, pix = png_pixels(chunks)
        except Exception as e:
            report.append(f"pixels error {e}")
            continue
        for method, secrets in scan_bitstreams(name, w, h, ch, pix):
            for s in secrets:
                report.append(f"lsb {method} {s}")
    open(os.path.join(OUT, "extract_report.txt"), "w").write("\n".join(report) + "\n")
    print("\n".join(line for line in report if "secret{" in line or line.startswith("== ")))


if __name__ == "__main__":
    main()
