#!/usr/bin/env python3
import re, struct, zlib, itertools, math
from pathlib import Path

SECRET_RE = re.compile(r"secret\{[0-9a-fA-F]{8}\}")
SECRET_INNER_RE = re.compile(r"secret\{([0-9a-fA-F]{8})\}")


def parse_ihdr_and_idat(png_bytes: bytes):
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError('not png')
    pos = 8
    width = height = bit_depth = color_type = interlace = None
    channels = None
    idat_chunks = []
    while pos + 12 <= len(png_bytes):
        ln = struct.unpack('>I', png_bytes[pos:pos+4])[0]
        typ = png_bytes[pos+4:pos+8]
        cdata = png_bytes[pos+8:pos+8+ln]
        pos = pos + 12 + ln
        # crc skipped
        if typ == b'IHDR':
            width, height, bit_depth, color_type, _cm, _fm, interlace = struct.unpack('>IIBBBBB', cdata)
            if bit_depth != 8 or interlace != 0:
                raise ValueError('unsupported ihdr')
            if color_type == 2:
                channels = 3
            elif color_type == 6:
                channels = 4
            else:
                raise ValueError('unsupported color_type')
        elif typ == b'IDAT':
            idat_chunks.append(cdata)
        elif typ == b'IEND':
            break
    return width, height, channels, idat_chunks


def paeth(a,b,c):
    p=a+b-c
    pa=abs(p-a); pb=abs(p-b); pc=abs(p-c)
    if pa<=pb and pa<=pc: return a
    if pb<=pc: return b
    return c


def decode_png_prefix(path: str, max_lines: int):
    png_bytes = Path(path).read_bytes()
    w,h,ch,idat_chunks = parse_ihdr_and_idat(png_bytes)
    lines = min(max_lines, h)
    stride = w*ch
    # Each scanline starts with filter byte.
    need_inflated = lines * (stride + 1)

    decomp = zlib.decompressobj()
    inflated = bytearray()
    for chunk in idat_chunks:
        inflated.extend(decomp.decompress(chunk))
        if len(inflated) >= need_inflated:
            break
    if len(inflated) < need_inflated:
        # fallback to all until we reach
        for chunk in idat_chunks:
            if len(inflated) >= need_inflated:
                break
            inflated.extend(decomp.decompress(chunk))
        if len(inflated) < need_inflated:
            raise RuntimeError(f"insufficient inflated {len(inflated)} need {need_inflated}")

    pix = bytearray()
    prev = bytearray(stride)
    off = 0
    for _ in range(lines):
        ftype = inflated[off]
        off += 1
        scan = inflated[off:off+stride]
        off += stride
        recon = bytearray(stride)
        if ftype == 0:
            recon[:] = scan
        elif ftype == 1:
            for i in range(stride):
                left = recon[i-ch] if i>=ch else 0
                recon[i] = (scan[i] + left) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                recon[i] = (scan[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = recon[i-ch] if i>=ch else 0
                recon[i] = (scan[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = recon[i-ch] if i>=ch else 0
                up = prev[i]
                up_left = prev[i-ch] if i>=ch else 0
                recon[i] = (scan[i] + paeth(left, up, up_left)) & 0xFF
        else:
            raise ValueError('bad filter')
        pix.extend(recon)
        prev = recon

    return w, lines, ch, pix


def bits_to_bytes(bits, msb_first: bool):
    # bits is list/iter of 0/1. Length must be multiple of 8.
    out = bytearray()
    n = len(bits)//8
    for j in range(n):
        chunk = bits[j*8:(j+1)*8]
        v=0
        if msb_first:
            for b in chunk:
                v=(v<<1)|b
        else:
            # first bit is LSB
            for i,b in enumerate(chunk):
                v |= (b<<i)
        out.append(v)
    return out


def search_secret_from_bits(bitstream_bits, max_bytes=64):
    bits_len = len(bitstream_bits)
    needed_bits_maxoffset = 8*max_bytes + 7
    if bits_len < needed_bits_maxoffset:
        # still try best effort using max available
        pass
    found = set()
    for offset in range(0, min(8, bits_len)):
        avail = bits_len - offset
        nbytes = min(max_bytes, avail//8)
        if nbytes <= 0:
            continue
        for msb_first in (True, False):
            sl = bitstream_bits[offset:offset+8*nbytes]
            b = bits_to_bytes(sl, msb_first=msb_first)
            s = b.decode('latin1', errors='ignore')
            m = SECRET_INNER_RE.search(s)
            if m:
                found.add('secret{' + m.group(1).lower() + '}')
    return found


def make_channel_sets(ch):
    # returns list of (name, channel_indices_in_order)
    if ch == 3:
        return [
            ('r', [0]),
            ('g', [1]),
            ('b', [2]),
            ('rgb', [0,1,2]),
            ('bgr', [2,1,0]),
        ]
    elif ch == 4:
        return [
            ('r', [0]),
            ('g', [1]),
            ('b', [2]),
            ('a', [3]),
            ('rgb', [0,1,2]),
            ('rgba', [0,1,2,3]),
        ]
    raise ValueError('bad ch')


def extract_bits(w, lines, ch, pix, order: str, ci_list):
    # pix corresponds to first `lines` scanlines unfiltered, in normal row-major byte order.
    # We'll generate bitstream bits by iterating pixels in specified order and taking bits
    # from channels in ci_list (in that order).
    if order == 'row':
        # y outer, x inner
        for y in range(lines):
            rowoff = y*w*ch
            for x in range(w):
                base = rowoff + x*ch
                for ci in ci_list:
                    yield pix[base + ci]
    elif order == 'col':
        # x outer, y inner
        for x in range(w):
            for y in range(lines):
                base = (y*w + x)*ch
                for ci in ci_list:
                    yield pix[base + ci]
    else:
        raise ValueError(order)


def find_secret_for_image(img_path: str):
    # We only need 4 missing images; brute-force limited to prefix rows and small output window.
    max_bytes = 64
    # Ensure we have enough lines for both row and col: for row, often 1 line enough; for col, need enough rows.
    # Worst-case: single channel => 1 bit per pixel => need 8*max_bytes+7 bits.
    # bits per pixel = len(ci_list). For col order, bits consume sequential by y so need ~needed_bits/len(ci_list) rows.
    needed_bits = 8*max_bytes + 7
    # cap lines to keep decoding manageable
    max_lines_cap = 800

    # decode with some reasonable default lines; we'll increase if needed for col.
    w, h, ch, _idat = parse_ihdr_and_idat(Path(img_path).read_bytes())
    ci_sets = make_channel_sets(ch)
    max_lines = 0
    for _, ci_list in ci_sets:
        lines_needed = math.ceil(needed_bits / max(1, len(ci_list)))
        max_lines = max(max_lines, lines_needed)
    max_lines = min(max_lines, max_lines_cap)
    w, lines, ch, pix = decode_png_prefix(img_path, max_lines=max_lines)

    for bit in range(8):
        for order in ('row','col'):
            for cname, ci_list in ci_sets:
                # Build bitstream of just enough values to cover bits needed.
                # Each yielded pixel value contributes 1 bit at position `bit`.
                # For row order, sequence length grows quickly, so we can stop once enough.
                bitstream=[]
                max_vals = (needed_bits // 1) + 16
                # Generate values until we have enough bits for max offset.
                for v in extract_bits(w, lines, ch, pix, order, ci_list):
                    bitstream.append((v >> bit) & 1)
                    if len(bitstream) >= needed_bits:
                        break
                found = search_secret_from_bits(bitstream, max_bytes=max_bytes)
                if found:
                    # return first found in stable order
                    return next(iter(sorted(found)))
    return None


def main():
    targets = ['puzzle_0009.png','puzzle_0010.png','puzzle_0011.png','puzzle_0012.png']
    for t in targets:
        sec = find_secret_for_image(t)
        print(t, sec or '<NOT_FOUND>')

if __name__ == '__main__':
    main()
