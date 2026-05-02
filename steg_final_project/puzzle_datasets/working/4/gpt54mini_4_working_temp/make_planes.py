#!/usr/bin/env python3
import glob
import os
import struct
import zlib


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


def decode_png(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    off = 8
    width = height = bit_depth = color_type = None
    idat = []
    while off + 12 <= len(data):
        length = struct.unpack(">I", data[off : off + 4])[0]
        ctype = data[off + 4 : off + 8]
        cdata = data[off + 8 : off + 8 + length]
        off += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", cdata
            )
        elif ctype == b"IDAT":
            idat.append(cdata)
        elif ctype == b"IEND":
            break
    if bit_depth != 8:
        raise ValueError("only 8-bit supported")
    if color_type == 0:
        channels = 1
    elif color_type == 2:
        channels = 3
    elif color_type == 4:
        channels = 2
    elif color_type == 6:
        channels = 4
    else:
        raise ValueError(f"unsupported color type {color_type}")
    raw = zlib.decompress(b"".join(idat))
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
            raise ValueError(f"bad filter {filt}")
        out[y * stride : (y + 1) * stride] = recon
        prev = recon
    return width, height, channels, bytes(out)


def write_pgm(path, w, h, pix):
    with open(path, "wb") as f:
        f.write(f"P5\n{w} {h}\n255\n".encode())
        f.write(pix)


def main():
    outdir = "working_temp/planes"
    os.makedirs(outdir, exist_ok=True)
    for path in sorted(glob.glob("puzzle_*.png")):
        w, h, ch, pix = decode_png(path)
        basename = os.path.splitext(os.path.basename(path))[0]
        for chan in range(ch):
            vals = pix[chan::ch]
            for bit in (0, 1):
                plane = bytes((255 if ((b >> bit) & 1) else 0) for b in vals)
                out = os.path.join(outdir, f"{basename}_c{chan}_b{bit}.pgm")
                write_pgm(out, w, h, plane)
                print(out)


if __name__ == "__main__":
    main()
