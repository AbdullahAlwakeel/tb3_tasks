import os
import re
import sys

from PIL import Image


SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def build_channel_stream(data: bytes, w: int, h: int, channels: int, subset: tuple[int, ...], *, limit_bytes: int) -> bytes:
    row_bytes = w * channels
    out = bytearray()
    for y in range(h):
        row_base = y * row_bytes
        for x in range(w):
            pix_base = row_base + x * channels
            for c in subset:
                out.append(data[pix_base + c])
                if len(out) >= limit_bytes:
                    return bytes(out)
    return bytes(out)


def decode_from_2bit_stream(
    stream: bytes,
    *,
    start_bit: int,  # starting bit position for the 2-bit window (byte>>start_bit) & 0b11
    within_group_order: str,  # 'lsb-first' means lower of the two bits comes first in the global bitstream
    invert: int,
    offset_bits: int,
    pack_bit_order: str,  # how bits map into output bytes
    out_len_bytes: int,
) -> bytes:
    # Build bitstream bits for the first portion only; caller sets stream to a small prefix.
    # Extract bits for each source byte: two bits contiguous.
    bits = bytearray()
    mask = 0b11
    for b in stream:
        v = (b >> start_bit) & mask
        if within_group_order == "lsb-first":
            b0 = (v & 1)
            b1 = (v >> 1) & 1
        else:  # 'msb-first'
            b1 = (v & 1)
            b0 = (v >> 1) & 1
        if invert:
            b0 ^= 1
            b1 ^= 1
        bits.append(b0)
        bits.append(b1)

    # Decode bytes from bitstream with offset.
    if offset_bits + out_len_bytes * 8 > len(bits):
        # Not enough bits
        return b""

    out = bytearray(out_len_bytes)
    for i in range(out_len_bytes):
        acc = 0
        for k in range(8):
            bit = bits[offset_bits + i * 8 + k]
            if pack_bit_order == "msb":
                acc = (acc << 1) | bit
            else:
                acc |= bit << k
        out[i] = acc
    return bytes(out)


def main():
    if len(sys.argv) < 2:
        print("Usage: bruteforce_2bit_pillow.py <dir> [max_source_bytes] [out_len_bytes]")
        return 2
    root = sys.argv[1]
    max_source_bytes = int(sys.argv[2]) if len(sys.argv) >= 3 else 250  # 2 bits/byte -> enough for ~15-32 bytes
    out_len_bytes = int(sys.argv[3]) if len(sys.argv) >= 4 else 32

    pngs = sorted([p for p in os.listdir(root) if p.startswith("puzzle_") and p.endswith(".png")])
    if not pngs:
        print("No puzzle_*.png files found in", root)
        return 2

    subsets_rgb = [(0,), (1,), (2,), (0, 1), (1, 2), (0, 2), (0, 1, 2)]
    subsets_rgba = [
        (0,),
        (1,),
        (2,),
        (3,),
        (0, 1),
        (1, 2),
        (0, 2),
        (0, 1, 2),
        (0, 1, 2, 3),
        (3,),
        (0, 3),
        (1, 3),
        (2, 3),
        (0, 1, 3),
        (1, 2, 3),
        (0, 2, 3),
        (0, 1, 2, 3),
    ]

    def dedup(seq):
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    subsets_rgba = dedup(subsets_rgba)

    # Limit offset bits (global bit offset within the first byte boundary) to keep runtime down.
    offset_bits_values = list(range(0, 16))

    for name in pngs:
        path = os.path.join(root, name)
        img = Image.open(path)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        channels = 3 if img.mode == "RGB" else 4
        w, h = img.size
        data = img.tobytes()
        subsets = subsets_rgb if channels == 3 else subsets_rgba

        found = None
        # Try both forward and reversed stream.
        for stream_rev in (False, True):
            if found:
                break
            for subset in subsets:
                if found:
                    break
                stream = build_channel_stream(data, w, h, channels, subset, limit_bytes=max_source_bytes)
                if stream_rev:
                    stream = stream[::-1]

                for start_bit in range(0, 7):  # 2-bit window start: 0..6
                    for within_order in ("lsb-first", "msb-first"):
                        for invert in (0, 1):
                            for pack_bit_order in ("msb", "lsb"):
                                for offset_bits in offset_bits_values:
                                    decoded = decode_from_2bit_stream(
                                        stream,
                                        start_bit=start_bit,
                                        within_group_order=within_order,
                                        invert=invert,
                                        offset_bits=offset_bits,
                                        pack_bit_order=pack_bit_order,
                                        out_len_bytes=out_len_bytes,
                                    )
                                    if not decoded:
                                        continue
                                    m = SECRET_RE.search(decoded)
                                    if m:
                                        found = m.group(0).decode("ascii")
                                        break
                                    m2 = SECRET_RE.search(decoded[::-1])
                                    if m2:
                                        found = m2.group(0).decode("ascii")
                                        break
                                if found:
                                    break
                            if found:
                                break
                        if found:
                            break
                    if found:
                        break
        print(f"{name}: {found if found else 'NOT_FOUND'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

