import os
import re
import sys
from itertools import product

from PIL import Image


SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def build_channel_stream(data: bytes, w: int, h: int, channels: int, subset: tuple[int, ...], *, limit_bytes: int) -> bytes:
    # Row-major pixels, and within each pixel output channel bytes in subset order.
    # channels is 3 (RGB) or 4 (RGBA).
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


def bits_from_stream(stream: bytes, start_bit: int, *, invert: int) -> list[int]:
    if start_bit < 0 or start_bit > 7:
        raise ValueError("start_bit out of range")
    mask = 1 << start_bit
    inv = 0xFF if invert else 0
    bits = []
    for b in stream:
        bit = 1 if (b & mask) else 0
        if invert:
            bit ^= 1
        bits.append(bit)
    return bits


def decode_bytes_from_bits(bits: list[int], *, offset_bits: int, pack_bit_order: str, out_len: int) -> bytes:
    # offset_bits is bit index into `bits`.
    if offset_bits >= len(bits):
        return b""
    needed = offset_bits + out_len * 8
    if needed > len(bits):
        bits = bits[:needed]

    out = bytearray(out_len)
    for i in range(out_len):
        acc = 0
        for k in range(8):
            bit = bits[offset_bits + i * 8 + k]
            if pack_bit_order == "msb":
                acc = (acc << 1) | bit
            else:
                acc |= bit << k
        out[i] = acc
    return bytes(out)


def search_secret_from_mapping(stream: bytes, *, start_bit: int, invert: int, offset_bits: int, pack_bit_order: str, out_len: int) -> str | None:
    # Stream provides one bit per byte: bit at `start_bit` (LSB index convention like (byte>>start_bit)&1).
    bits = bits_from_stream(stream, start_bit, invert=invert)
    decoded = decode_bytes_from_bits(bits, offset_bits=offset_bits, pack_bit_order=pack_bit_order, out_len=out_len)
    m = SECRET_RE.search(decoded)
    if m:
        return m.group(0).decode("ascii")
    # also check if decoder accidentally needs reversed bytes
    m2 = SECRET_RE.search(decoded[::-1])
    if m2:
        return m2.group(0).decode("ascii")
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: bruteforce_singlebit_pillow.py <dir> [max_source_bytes] [out_len_bytes]")
        return 2

    root = sys.argv[1]
    max_source_bytes = int(sys.argv[2]) if len(sys.argv) >= 3 else 600  # enough for 15 bytes at 1 bit/byte
    out_len_bytes = int(sys.argv[3]) if len(sys.argv) >= 4 else 64  # search within first chunk
    if out_len_bytes > 32:
        out_len_bytes = 32

    pngs = sorted([p for p in os.listdir(root) if p.startswith("puzzle_") and p.endswith(".png")])
    if not pngs:
        print("No puzzle_*.png files found in", root)
        return 2

    # Channel subsets to try (keeps order as given).
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

    # Deduplicate while preserving order
    def dedup(seq):
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    subsets_rgba = dedup(subsets_rgba)

    total_params = 8 * 8 * 2 * 2 * len(subsets_rgb)  # rough
    # print("param grid ~", total_params)

    for name in pngs:
        path = os.path.join(root, name)
        img = Image.open(path)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

        channels = 3 if img.mode == "RGB" else 4
        w, h = img.size
        data = img.tobytes()

        if channels == 3:
            subsets = subsets_rgb
        else:
            subsets = subsets_rgba

        found = None
        # Stream reversal can matter if extraction reads bits backwards.
        for stream_bytes in (
            None,
            "rev",
        ):
            for subset in subsets:
                stream = build_channel_stream(data, w, h, channels, subset, limit_bytes=max_source_bytes)
                if stream_bytes == "rev":
                    stream = stream[::-1]

                if not stream:
                    continue

                # Cache extracted bitplanes for each (start_bit, invert) to avoid recomputation.
                bitplane_cache: dict[tuple[int, int], list[int]] = {}
                for start_bit in range(8):
                    for invert in (0, 1):
                        bitplane_cache[(start_bit, invert)] = bits_from_stream(stream, start_bit, invert=invert)

                for start_bit in range(8):
                    for invert in (0, 1):
                        bits = bitplane_cache[(start_bit, invert)]
                        for offset_bits in range(8):
                            for pack_bit_order in ("msb", "lsb"):
                                decoded = decode_bytes_from_bits(
                                    bits,
                                    offset_bits=offset_bits,
                                    pack_bit_order=pack_bit_order,
                                    out_len=out_len_bytes,
                                )
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
            if found:
                break
            if found:
                break

        print(f"{name}: {found if found else 'NOT_FOUND'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
