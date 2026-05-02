from pathlib import Path
import struct
import sys
import zlib


def decode_png(path: Path):
    data = path.read_bytes()
    pos = 8
    idat = bytearray()
    color_type = None
    width = height = None
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        cdata = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, comp, flt, inter = struct.unpack(
                ">IIBBBBB", cdata
            )
        elif ctype == b"IDAT":
            idat.extend(cdata)
        elif ctype == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    bpp = 3 if color_type == 2 else 4 if color_type == 6 else 1
    stride = width * bpp
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        filt = raw[i]
        i += 1
        scan = bytearray(raw[i : i + stride])
        i += stride
        if filt == 0:
            row = scan
        elif filt == 1:
            row = bytearray(scan)
            for j in range(bpp, stride):
                row[j] = (row[j] + row[j - bpp]) & 0xFF
        elif filt == 2:
            row = bytearray(scan)
            for j in range(stride):
                row[j] = (row[j] + prev[j]) & 0xFF
        elif filt == 3:
            row = bytearray(scan)
            for j in range(stride):
                left = row[j - bpp] if j >= bpp else 0
                up = prev[j]
                row[j] = (row[j] + ((left + up) >> 1)) & 0xFF
        else:
            row = bytearray(scan)
            for j in range(stride):
                a = row[j - bpp] if j >= bpp else 0
                b = prev[j]
                c = prev[j - bpp] if j >= bpp else 0
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[j] = (row[j] + pr) & 0xFF
        out.extend(row)
        prev = row
    return width, height, color_type, bytes(out)


def main(argv):
    invert = False
    if argv and argv[-1] == "--invert":
        invert = True
        argv = argv[:-1]
    if len(argv) < 2:
        print("usage: enhance_view.py input.png output.pgm [x y w h] [--invert]")
        raise SystemExit(2)
    inp = Path(argv[0])
    out = Path(argv[1])
    x = y = 0
    w = h = None
    if len(argv) == 6:
        x, y, w, h = map(int, argv[2:6])
    width, height, ct, pix = decode_png(inp)
    chans = 3 if ct == 2 else 4
    if w is None:
        w, h = width, height
    pgm = bytearray()
    pgm.extend(f"P5\n{w} {h}\n255\n".encode())
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            i = (yy * width + xx) * chans
            r, g, b = pix[i : i + 3]
            gray = (r * 30 + g * 59 + b * 11) // 100
            if gray > 235:
                gray = 255
            elif gray < 20:
                gray = 0
            if invert:
                gray = 255 - gray
            pgm.append(gray)
    out.write_bytes(pgm)


if __name__ == "__main__":
    main(sys.argv[1:])
