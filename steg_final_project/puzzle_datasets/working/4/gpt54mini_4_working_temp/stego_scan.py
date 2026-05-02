#!/usr/bin/env python3
import base64
import binascii
import glob
import io
import os
import re
import struct
import sys
import zlib

SECRET_RE = re.compile(rb"secret\{[0-9a-f]{8}\}")


def read_png(path):
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not png")
    off = 8
    chunks = []
    width = height = bit_depth = color_type = None
    idat_parts = []
    after_iend = b""
    while off + 12 <= len(data):
        length = struct.unpack(">I", data[off : off + 4])[0]
        ctype = data[off + 4 : off + 8]
        cdata = data[off + 8 : off + 8 + length]
        off += 12 + length
        chunks.append((ctype, cdata))
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", cdata
            )
        elif ctype == b"IDAT":
            idat_parts.append(cdata)
        elif ctype == b"IEND":
            after_iend = data[off:]
            break
    return {
        "data": data,
        "chunks": chunks,
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "idat": b"".join(idat_parts),
        "after_iend": after_iend,
    }


def paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_pixels(meta):
    width = meta["width"]
    height = meta["height"]
    bit_depth = meta["bit_depth"]
    color_type = meta["color_type"]
    if bit_depth != 8:
        raise NotImplementedError("only 8-bit PNGs supported")
    if color_type == 0:
        channels = 1
    elif color_type == 2:
        channels = 3
    elif color_type == 4:
        channels = 2
    elif color_type == 6:
        channels = 4
    else:
        raise NotImplementedError(f"unsupported color type {color_type}")
    raw = zlib.decompress(meta["idat"])
    stride = width * channels
    out = bytearray(height * stride)
    pos = 0
    prev = bytearray(stride)
    for y in range(height):
        filt = raw[pos]
        pos += 1
        scan = bytearray(raw[pos : pos + stride])
        pos += stride
        if filt == 0:
            recon = scan
        elif filt == 1:
            recon = bytearray(stride)
            for i in range(stride):
                left = recon[i - channels] if i >= channels else 0
                recon[i] = (scan[i] + left) & 0xFF
        elif filt == 2:
            recon = bytearray(stride)
            for i in range(stride):
                recon[i] = (scan[i] + prev[i]) & 0xFF
        elif filt == 3:
            recon = bytearray(stride)
            for i in range(stride):
                left = recon[i - channels] if i >= channels else 0
                up = prev[i]
                recon[i] = (scan[i] + ((left + up) >> 1)) & 0xFF
        elif filt == 4:
            recon = bytearray(stride)
            for i in range(stride):
                left = recon[i - channels] if i >= channels else 0
                up = prev[i]
                ul = prev[i - channels] if i >= channels else 0
                recon[i] = (scan[i] + paeth(left, up, ul)) & 0xFF
        else:
            raise ValueError(f"unknown filter {filt}")
        out[y * stride : (y + 1) * stride] = recon
        prev = recon
    return bytes(out), channels


def extract_bits(data, bit, lsb_first=True):
    bits = [(b >> bit) & 1 for b in data]
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        chunk = bits[i : i + 8]
        if lsb_first:
            val = sum(bit << j for j, bit in enumerate(chunk))
        else:
            val = 0
            for bitv in chunk:
                val = (val << 1) | bitv
        out.append(val)
    return bytes(out)


def candidate_streams(pixels, channels):
    # Different byte orders and channel subsets.
    subset_orders = []
    if channels == 1:
        subset_orders = [(0,)]
    elif channels == 2:
        subset_orders = [(0,), (1,), (0, 1), (1, 0)]
    elif channels == 3:
        subset_orders = [
            (0,),
            (1,),
            (2,),
            (0, 1, 2),
            (2, 1, 0),
            (1, 0, 2),
        ]
    elif channels == 4:
        subset_orders = [
            (0,),
            (1,),
            (2,),
            (3,),
            (0, 1, 2),
            (0, 1, 2, 3),
            (2, 1, 0, 3),
            (3, 2, 1, 0),
        ]
    for order in subset_orders:
        seq = bytearray()
        for i in range(0, len(pixels), channels):
            for idx in order:
                seq.append(pixels[i + idx])
        yield f"channels={order}", bytes(seq)


def scan_file(path):
    meta = read_png(path)
    text_hits = []
    for ctype, cdata in meta["chunks"]:
        if ctype in {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"iCCP"}:
            text_hits.append((ctype.decode(), len(cdata), cdata[:80]))
    hits = []
    if SECRET_RE.search(meta["data"]):
        hits.append(("raw", SECRET_RE.search(meta["data"]).group().decode()))
    if meta["after_iend"]:
        trailer = meta["after_iend"]
        m = SECRET_RE.search(trailer)
        if m:
            hits.append(("trailer", m.group().decode()))
        # try len-prefixed base64 or direct payloads
        if len(trailer) >= 4:
            n = struct.unpack(">I", trailer[:4])[0]
            body = trailer[4 : 4 + n]
            for label, blob in [("lenbody", body), ("trail", trailer)]:
                for decoder_name, decoder in [
                    ("b64", base64.b64decode),
                    ("b85", base64.b85decode),
                ]:
                    try:
                        dec = decoder(blob)
                    except Exception:
                        continue
                    m = SECRET_RE.search(dec)
                    if m:
                        hits.append((f"{label}-{decoder_name}", m.group().decode()))
    # bitstream scans
    try:
        pixels, channels = decode_png_pixels(meta)
    except Exception as e:
        return text_hits, hits, [("decode_error", str(e))]
    for label, seq in candidate_streams(pixels, channels):
        for bit in range(8):
            for lsb_first in [True, False]:
                stream = extract_bits(seq, bit, lsb_first=lsb_first)
                m = SECRET_RE.search(stream)
                if m:
                    hits.append((f"{label}:bit{bit}:{'lsb' if lsb_first else 'msb'}", m.group().decode()))
    return text_hits, hits, []


def main():
    files = sorted(glob.glob("puzzle_*.png"))
    for f in files:
        text_hits, hits, errs = scan_file(f)
        if hits or text_hits or errs:
            print(f"== {f} ==")
            for x in text_hits:
                print("TEXT", x)
            for x in hits:
                print("HIT", x)
            for x in errs:
                print("ERR", x)


if __name__ == "__main__":
    main()
