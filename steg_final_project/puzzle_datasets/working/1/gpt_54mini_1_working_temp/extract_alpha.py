#!/usr/bin/env python3
import struct
import sys
import zlib
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"


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
            if bitdepth != 8 or inter != 0 or colortype != 6:
                raise ValueError(f"unsupported png params: bd={bitdepth} ct={colortype} inter={inter}")
        elif ctype == b"IDAT":
            idat.extend(chunk)
        elif ctype == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    return width, height, raw


def unfilter_rgba(width, height, raw):
    channels = 4
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
    return bytes(out)


def write_gray_png(path, width, height, pixels):
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y*width:(y+1)*width])
    comp = zlib.compress(bytes(raw), 9)
    def chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xffffffff)
    png = bytearray(PNG_SIG)
    png.extend(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)))
    png.extend(chunk(b"IDAT", comp))
    png.extend(chunk(b"IEND", b""))
    Path(path).write_bytes(bytes(png))


def main():
    outdir = Path(sys.argv[1])
    outdir.mkdir(parents=True, exist_ok=True)
    for src in sys.argv[2:]:
        w, h, raw = read_png(src)
        pix = unfilter_rgba(w, h, raw)
        alpha = bytearray(w * h)
        for i in range(w * h):
            alpha[i] = pix[i * 4 + 3]
        write_gray_png(outdir / f"{Path(src).stem}_alpha.png", w, h, alpha)


if __name__ == "__main__":
    main()
