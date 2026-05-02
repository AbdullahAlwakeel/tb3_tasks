#!/usr/bin/env python3
import os
import struct
import sys
import zlib
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def read_png(path):
    data = Path(path).read_bytes()
    if not data.startswith(PNG_SIG):
        raise ValueError(f"not png: {path}")
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
                raise ValueError(f"unsupported png params: bd={bitdepth} inter={inter}")
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


def write_png_gray(path, width, height, pixels):
    # pixels: bytes length width*height, one byte per pixel grayscale
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        start = y * width
        raw.extend(pixels[start:start+width])
    comp = zlib.compress(bytes(raw), level=9)
    def chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xffffffff)
    png = bytearray(PNG_SIG)
    png.extend(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)))
    png.extend(chunk(b"IDAT", comp))
    png.extend(chunk(b"IEND", b""))
    Path(path).write_bytes(bytes(png))


def make_bitplanes(src, outdir):
    width, height, ctype, raw = read_png(src)
    pixels, channels = unfilter(width, height, ctype, raw)
    base = Path(outdir)
    base.mkdir(parents=True, exist_ok=True)
    for ch in range(channels):
        for bit in (0, 1, 2):
            out = bytearray(width * height)
            for i in range(width * height):
                v = pixels[i * channels + ch]
                out[i] = 255 if ((v >> bit) & 1) else 0
            write_png_gray(base / f"{Path(src).stem}_ch{ch}_bit{bit}.png", width, height, out)


def main():
    outdir = sys.argv[1]
    for src in sys.argv[2:]:
        make_bitplanes(src, outdir)


if __name__ == "__main__":
    main()
