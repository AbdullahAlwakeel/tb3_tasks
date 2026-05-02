import os
import re
import sys
from typing import Callable, Iterable

from PIL import Image


SECRET_RE = re.compile(r"secret\{[0-9a-fA-F]{8}\}")


def to_bytes_prefix(seq: Iterable[int], max_len: int) -> bytes:
    out = bytearray()
    for b in seq:
        out.append(b)
        if len(out) >= max_len:
            break
    return bytes(out)


def pack_bits(bits, offset, bit_order, max_bytes):
    # bits is list[int] of 0/1
    start = offset
    if start >= len(bits):
        return b""
    max_groups = (len(bits) - start) // 8
    max_groups = min(max_groups, max_bytes)
    out = bytearray(max_groups)
    idx = 0
    for j in range(max_groups):
        val = 0
        for k in range(8):
            bit = bits[start + j * 8 + k]
            if bit_order == "msb":
                val = (val << 1) | bit
            else:
                val |= (bit << k)
        out[idx] = val
        idx += 1
    return bytes(out)


def try_extract_from_byte_prefix(prefix: bytes, *, max_packed_bytes: int) -> str | None:
    # We only need enough source bytes to cover offsets within the first `max_packed_bytes`
    # output bytes. Each source byte contributes 1 bit.
    offset_max = 7
    needed_source_bytes = offset_max + max_packed_bytes * 8
    prefix = prefix[:needed_source_bytes]

    # Bit extraction modes:
    #  - const: use a fixed bit_pos from every source byte
    #  - cyclic: use bit ((base + i) % 8) from source byte i
    for mode in ("const", "cyclic"):
        if mode == "const":
            bases = list(range(8))
        else:
            bases = list(range(8))

        for base in bases:
            if mode == "const":
                bit_pos_fixed = base
                bits = [((b >> bit_pos_fixed) & 1) for b in prefix]
            else:
                # cyclic: base shifts the mapping
                bits = [((prefix[i] >> ((base + i) & 7)) & 1) for i in range(len(prefix))]

            for bit_inv in (0, 1):
                bits2 = bits if bit_inv == 0 else [x ^ 1 for x in bits]

                for bit_order in ("msb", "lsb"):
                    for offset in range(8):
                        packed = pack_bits(bits2, offset, bit_order, max_packed_bytes)
                        if not packed:
                            continue

                        s = packed.decode("latin1", errors="ignore")
                        m = SECRET_RE.search(s)
                        if m:
                            return m.group(0)

                        s2 = packed[::-1].decode("latin1", errors="ignore")
                        m2 = SECRET_RE.search(s2)
                        if m2:
                            return m2.group(0)

    return None


def build_sequences(w: int, h: int, channels: int, data: bytes) -> list[Callable[[int], bytes]]:
    row_bytes = w * channels

    def seq_row_major(max_len: int) -> bytes:
        return data[:max_len]

    def seq_row_major_rev(max_len: int) -> bytes:
        # reverse within prefix region
        return data[:max_len][::-1]

    def seq_serpentine(max_len: int) -> bytes:
        out = bytearray()
        for y in range(h):
            row = data[y * row_bytes : (y + 1) * row_bytes]
            if y % 2 == 1:
                row = row[::-1]
            need = max_len - len(out)
            if need <= 0:
                break
            out += row[:need]
        return bytes(out)

    def seq_column_major(max_len: int) -> bytes:
        # Visit pixels column-first (x then y), channels in byte order within pixel.
        out = bytearray()
        for x in range(w):
            if len(out) >= max_len:
                break
            for y in range(h):
                if len(out) >= max_len:
                    break
                p = (y * w + x) * channels
                out += data[p : p + channels]
                if len(out) >= max_len:
                    break
        return bytes(out[:max_len])

    def seq_channel_group(max_len: int) -> bytes:
        # For channels C0..C{channels-1}, concatenate all bytes of each channel
        # in pixel order (row-major), channel bytes within a pixel remain untouched.
        # This matches embeddings that read "all R bits then all G bits", etc.
        out = bytearray()
        for c in range(channels):
            for y in range(h):
                base = y * row_bytes
                for x in range(w):
                    idx = base + x * channels + c
                    out.append(data[idx])
                    if len(out) >= max_len:
                        return bytes(out[:max_len])
        return bytes(out)

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

    return [
        seq_row_major,
        # Single-channel extraction (common for LSB stego)
        *[seq_channel_only(c) for c in range(channels)],
        seq_serpentine,
        seq_column_major,
        seq_channel_group,
        seq_row_major_rev,
    ]


def main():
    if len(sys.argv) < 2:
        print("Usage: extract_secrets_pillow_robust.py <dir> [max_prefix_bytes] [max_packed_bytes]")
        return 2

    root = sys.argv[1]
    max_prefix_bytes = int(sys.argv[2]) if len(sys.argv) >= 3 else 20000
    max_packed_bytes = int(sys.argv[3]) if len(sys.argv) >= 4 else 512

    pngs = sorted([p for p in os.listdir(root) if p.startswith("puzzle_") and p.endswith(".png")])
    if not pngs:
        print("No puzzle_*.png found in", root)
        return 2

    for name in pngs:
        path = os.path.join(root, name)
        img = Image.open(path)
        if img.mode not in ("RGB", "RGBA"):
            # Normalize to either RGB or RGBA depending on presence of alpha.
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
            found = try_extract_from_byte_prefix(prefix, max_packed_bytes=max_packed_bytes)
            if found:
                break

        if found:
            print(f"{name}: {found}")
        else:
            print(f"{name}: NOT_FOUND")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
