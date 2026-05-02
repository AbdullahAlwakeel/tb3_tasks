from pathlib import Path
import re
import struct
import sys
import zlib

SECRET_RE = re.compile(rb"secret\{[0-9a-f]{8}\}")


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
    return color_type, bytes(out)


def pack_bits(bits, msb=True):
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        chunk = bits[i : i + 8]
        byte = 0
        if msb:
            for b in chunk:
                byte = (byte << 1) | b
        else:
            for j, b in enumerate(chunk):
                byte |= b << j
        out.append(byte)
    return bytes(out)


def scan(path: Path):
    ct, pix = decode_png(path)
    hits = []
    if SECRET_RE.search(pix):
        hits.extend(SECRET_RE.findall(pix))
        print(path.name, "direct", hits[-1].decode())
    sources = [("all", pix)]
    if ct == 6:
        sources.append(("alpha", pix[3::4]))
    for source_name, src in sources:
        for bit in range(8):
            bits = [(b >> bit) & 1 for b in src]
            for msb in (True, False):
                packed = pack_bits(bits, msb)
                m = SECRET_RE.findall(packed)
                if m:
                    s = m[0].decode()
                    hits.append(m[0])
                    print(path.name, source_name, f"bit{bit}", "msb" if msb else "lsb", s)
    return hits


def main(argv):
    total = 0
    for arg in argv:
        p = Path(arg)
        if not p.exists():
            continue
        try:
            total += len(scan(p))
        except Exception as e:
            print(p.name, "ERROR", e)
    print("TOTAL_HITS", total)


if __name__ == "__main__":
    main(sys.argv[1:])
