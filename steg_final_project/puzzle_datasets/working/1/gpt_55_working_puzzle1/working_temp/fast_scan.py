#!/usr/bin/env python3
import base64
import binascii
import glob
import os
import re
import struct
import subprocess
import zlib

import numpy as np
from PIL import Image, ImageOps

SECRET = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "working_temp")
BITOUT = os.path.join(OUT, "bitplanes")
METAOUT = os.path.join(OUT, "metadata_payloads")
os.makedirs(BITOUT, exist_ok=True)
os.makedirs(METAOUT, exist_ok=True)


def hits(data):
    return sorted(set(x.decode("ascii").lower() for x in SECRET.findall(data)))


def parse_png(path):
    data = open(path, "rb").read()
    pos = 8
    chunks = []
    while pos + 12 <= len(data):
        ln = struct.unpack(">I", data[pos:pos+4])[0]
        typ = data[pos+4:pos+8]
        cdata = data[pos+8:pos+8+ln]
        chunks.append((typ, cdata, pos, ln))
        pos += 12 + ln
        if typ == b"IEND":
            break
    return data, chunks, data[pos:]


def decode_candidates(label, data):
    out = []
    out += [(label, h) for h in hits(data)]
    compact = re.sub(rb"\s+", b"", data)
    for kind in ["b64", "b32", "hex"]:
        try:
            if kind == "b64":
                dec = base64.b64decode(compact + b"=" * ((4 - len(compact) % 4) % 4), validate=False)
            elif kind == "b32":
                dec = base64.b32decode(compact + b"=" * ((8 - len(compact) % 8) % 8), casefold=True)
            else:
                if len(compact) % 2:
                    continue
                dec = binascii.unhexlify(compact)
        except Exception:
            continue
        out += [(label + ":" + kind, h) for h in hits(dec)]
        if dec.startswith(b"\x89PNG"):
            fn = os.path.join(METAOUT, label.replace("/", "_") + f".{kind}.png")
            open(fn, "wb").write(dec)
        elif dec.startswith(b"\xff\xd8"):
            fn = os.path.join(METAOUT, label.replace("/", "_") + f".{kind}.jpg")
            open(fn, "wb").write(dec)
        for comp in ["zlib", "gzip"]:
            try:
                dec2 = zlib.decompress(dec, 16 + zlib.MAX_WBITS if comp == "gzip" else zlib.MAX_WBITS)
            except Exception:
                continue
            out += [(label + ":" + kind + ":" + comp, h) for h in hits(dec2)]
    return out


def pack_and_search(bits):
    ret = []
    if bits.size < 8:
        return ret
    n = (bits.size // 8) * 8
    b = bits[:n].astype(np.uint8)
    ret.append(("msb", np.packbits(b, bitorder="big").tobytes()))
    ret.append(("lsb", np.packbits(b, bitorder="little").tobytes()))
    return ret


def lsb_scan(path):
    im = Image.open(path)
    arr = np.array(im)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    h, w, ch = arr.shape
    labels = "rgba"[:ch]
    channel_sets = [(labels[i], [i]) for i in range(ch)]
    if ch >= 3:
        channel_sets += [("rgb", [0, 1, 2]), ("bgr", [2, 1, 0])]
    channel_sets.append(("all", list(range(ch))))
    arrays = {
        "row": arr,
        "row_rev": arr[:, ::-1, :],
        "col": np.transpose(arr, (1, 0, 2)),
        "col_rev": np.transpose(arr[::-1, :, :], (1, 0, 2)),
    }
    out = []
    for bit in range(8):
        for cname, cis in channel_sets:
            for oname, a in arrays.items():
                bits = ((a[:, :, cis] >> bit) & 1).reshape(-1)
                for bord, blob in pack_and_search(bits):
                    for hhit in hits(blob):
                        out.append((f"bit{bit}:{cname}:{oname}:{bord}", hhit))
    return out


def save_bitplanes(path):
    name = os.path.splitext(os.path.basename(path))[0]
    im = Image.open(path)
    arr = np.array(im)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    ch = arr.shape[2]
    labels = "rgba"[:ch]
    made = []
    for bit in range(4):
        for ci, lab in enumerate(labels):
            plane = ((arr[:, :, ci] >> bit) & 1).astype(np.uint8) * 255
            img = Image.fromarray(plane, "L")
            # Downscale huge images only for quicker OCR/contact sheets.
            img.thumbnail((1600, 1600))
            fn = os.path.join(BITOUT, f"{name}_{lab}_bit{bit}.png")
            img.save(fn)
            made.append(fn)
    return made


def exif_text(path):
    try:
        p = subprocess.run(["exiftool", "-ImageDescription", "-UserComment", "-Comment", "-Keywords", "-b", path],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        return p.stdout
    except Exception:
        return b""


def main():
    lines = []
    found = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "puzzle_*.png"))):
        name = os.path.basename(path)
        data, chunks, trailer = parse_png(path)
        found[name] = []
        lines.append(f"== {name} ==")
        lines.append("chunks " + " ".join(f"{t.decode('latin1')}:{ln}" for t, _, _, ln in chunks))
        for where, hit in decode_candidates(f"{name}/raw", data):
            lines.append(f"{where} {hit}"); found[name].append(hit)
        if trailer:
            open(os.path.join(OUT, f"{name}.trailer.bin"), "wb").write(trailer)
            lines.append(f"trailer {len(trailer)} bytes")
            for where, hit in decode_candidates(f"{name}/trailer", trailer):
                lines.append(f"{where} {hit}"); found[name].append(hit)
        for typ, cdata, _, ln in chunks:
            if typ not in (b"IHDR", b"IDAT", b"IEND"):
                lines.append(f"chunk {typ.decode('latin1')} {ln}")
            if typ != b"IDAT":
                for where, hit in decode_candidates(f"{name}/{typ.decode('latin1')}", cdata):
                    lines.append(f"{where} {hit}"); found[name].append(hit)
        meta = exif_text(path)
        if meta:
            open(os.path.join(METAOUT, f"{name}.exif_text.bin"), "wb").write(meta)
            for where, hit in decode_candidates(f"{name}/exiftool", meta):
                lines.append(f"{where} {hit}"); found[name].append(hit)
        for method, hit in lsb_scan(path):
            lines.append(f"lsb {name} {method} {hit}")
            found[name].append(hit)
        save_bitplanes(path)
    open(os.path.join(OUT, "fast_scan_report.txt"), "w").write("\n".join(lines) + "\n")
    for name in sorted(found):
        uniq = sorted(set(found[name]))
        print(name, ", ".join(uniq) if uniq else "-")


if __name__ == "__main__":
    main()
