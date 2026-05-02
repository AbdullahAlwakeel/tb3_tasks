import os
import re
import sys
from typing import Callable

from PIL import Image


SECRET_RE_BYTES = re.compile(br"secret\{[0-9a-fA-F]{8}\}")


def pack_bits(bits, offset, bit_order, out_len_bytes):
    if offset >= len(bits):
        return b""
    start = offset
    available_bytes = (len(bits) - start) // 8
    n = min(available_bytes, out_len_bytes)
    out = bytearray(n)
    for j in range(n):
        v = 0
        for k in range(8):
            bit = bits[start + j * 8 + k]
            if bit_order == "msb":
                v = (v << 1) | bit
            else:
                v |= (bit << k)
        out[j] = v
    return bytes(out)


def try_extract_from_prefix(prefix: bytes, max_packed_bytes: int) -> str | None:
    # We only need enough source bits for the first max_packed_bytes output bytes.
    offset_max = 7
    needed_source_bits = offset_max + max_packed_bytes * 8
    needed_source_bytes = needed_source_bits  # 1 source bit per byte
    prefix = prefix[:needed_source_bytes]

    for bit_pos in range(8):
        bits = [((b >> bit_pos) & 1) for b in prefix]
        for bit_inv in (0, 1):
            if bit_inv:
                bits2 = [x ^ 1 for x in bits]
            else:
                bits2 = bits

            for bit_order in ("msb", "lsb"):
                for offset in range(8):
                    packed = pack_bits(bits2, offset, bit_order, max_packed_bytes)
                    if not packed:
                        continue
                    m = SECRET_RE_BYTES.search(packed)
                    if m:
                        return m.group(0).decode("ascii", errors="replace")
                    m = SECRET_RE_BYTES.search(packed[::-1])
                    if m:
                        return m.group(0).decode("ascii", errors="replace")
    return None


def build_sequences(w: int, h: int, channels: int, data: bytes) -> list[Callable[[int], bytes]]:
    row_bytes = w * channels

    def seq_row_major(max_len: int) -> bytes:
        return data[:max_len]

    def seq_channel_only(c: int):
        def _inner(max_len: int) -> bytes:
            out = bytearray()
            for y in range(h):
                base = y * row_bytes
                for x in range(w):
                    idx = base + x * channels + c
                    out.append(data[idx])
                    if len(out) >= max_len:
                        return bytes(out)
            return bytes(out)

        return _inner

    seqs = [seq_row_major]
    # Per-channel streams (common for LSB-in-one-channel challenges)
    for c in range(channels):
        seqs.append(seq_channel_only(c))

    # Add a couple cheap order variants
    def seq_row_major_rev(max_len: int) -> bytes:
        return data[:max_len][::-1]

    seqs.append(seq_row_major_rev)
    return seqs


def main():
    if len(sys.argv) < 2:
        print("Usage: extract_secrets_pillow_constant_scan.py <dir> [max_prefix_bytes] [max_packed_bytes]")
        return 2
    root = sys.argv[1]
    max_prefix_bytes = int(sys.argv[2]) if len(sys.argv) >= 3 else 600000
    max_packed_bytes = int(sys.argv[3]) if len(sys.argv) >= 4 else 5000

    pngs = sorted([p for p in os.listdir(root) if p.startswith("puzzle_") and p.endswith(".png")])
    if not pngs:
        print("No puzzle_*.png found in", root)
        return 2

    for name in pngs:
        path = os.path.join(root, name)
        img = Image.open(path)
        if img.mode not in ("RGB", "RGBA"):
            if "A" in img.getbands():
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")

        channels = 3 if img.mode == "RGB" else 4
        w, h = img.size
        data = img.tobytes()

        found = None
        seqs = build_sequences(w, h, channels, data)
        for seq in seqs:
            prefix = seq(max_prefix_bytes)
            found = try_extract_from_prefix(prefix, max_packed_bytes=max_packed_bytes)
            if found:
                break

        if found:
            print(f"{name}: {found}")
        else:
            print(f"{name}: NOT_FOUND")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

