import os
import re
import struct
import sys
import zlib


SECRET_RE = re.compile(br"secret\{[0-9a-fA-F]{8}\}")


def paeth_predictor(a: int, b: int, c: int) -> int:
    # PNG Paeth filter predictor
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_pixel_prefix(
    path: str, max_bytes: int, *, unfilter: bool = True
) -> tuple[int, int, bytes]:
    """
    Decode only the first `max_bytes` unfiltered pixel channel bytes from a PNG.
    Returns (channels_per_pixel, bytes_per_scanline, prefix_bytes).

    Supports only:
      - 8-bit samples
      - non-interlaced
      - color types 2 (RGB) and 6 (RGBA)
    """
    with open(path, "rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not a PNG")

        width = height = bit_depth = color_type = interlace = None
        idat_chunks: list[bytes] = []

        # Parse chunks until IHDR found and collect IDAT.
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                raise EOFError("Unexpected EOF while reading PNG chunks")
            length, typ = struct.unpack("!I4s", hdr)
            data = f.read(length)
            f.read(4)  # CRC (ignored)

            if typ == b"IHDR":
                width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                    "!IIBBBBB", data
                )
                if comp != 0 or filt != 0:
                    raise ValueError("Unsupported PNG compression/filter method")
                if interlace != 0:
                    raise ValueError("Interlaced PNG not supported")
                if bit_depth != 8:
                    raise ValueError(f"Unsupported bit depth: {bit_depth}")
                if color_type not in (2, 6):
                    raise ValueError(f"Unsupported color type: {color_type}")
            elif typ == b"IDAT":
                idat_chunks.append(data)
            elif typ == b"IEND":
                break

        if width is None:
            raise ValueError("Missing IHDR")

    # Only decode RGB/RGBA 8-bit.
    if color_type == 2:
        channels = 3
    else:
        channels = 4

    bytes_per_scanline = width * channels
    bpp = channels  # since bit_depth=8, bytes per pixel = channels

    # Incremental zlib decompress of concatenated IDAT.
    d = zlib.decompressobj()
    prefix = bytearray()
    prev_scanline = bytearray(bytes_per_scanline)  # zeros for scanline 0

    scanline_idx = 0
    leftover = b""

    for chunk in idat_chunks:
        out = d.decompress(chunk)
        if out:
            leftover += out

        while True:
            if len(leftover) < 1 + bytes_per_scanline:
                break
            if scanline_idx >= height:
                break

            filter_type = leftover[0]
            scanline_data = leftover[1 : 1 + bytes_per_scanline]
            leftover = leftover[1 + bytes_per_scanline :]

            if unfilter:
                recon = bytearray(bytes_per_scanline)
                if filter_type == 0:  # None
                    recon[:] = scanline_data
                elif filter_type == 1:  # Sub
                    for i, x in enumerate(scanline_data):
                        left = recon[i - bpp] if i >= bpp else 0
                        recon[i] = (x + left) & 0xFF
                elif filter_type == 2:  # Up
                    for i, x in enumerate(scanline_data):
                        up = prev_scanline[i]
                        recon[i] = (x + up) & 0xFF
                elif filter_type == 3:  # Average
                    for i, x in enumerate(scanline_data):
                        left = recon[i - bpp] if i >= bpp else 0
                        up = prev_scanline[i]
                        recon[i] = (x + ((left + up) >> 1)) & 0xFF
                elif filter_type == 4:  # Paeth
                    for i, x in enumerate(scanline_data):
                        left = recon[i - bpp] if i >= bpp else 0
                        up = prev_scanline[i]
                        up_left = prev_scanline[i - bpp] if i >= bpp else 0
                        recon[i] = (x + paeth_predictor(left, up, up_left)) & 0xFF
                else:
                    raise ValueError(f"Unsupported PNG filter type: {filter_type}")

                prev_scanline = recon
                scanline_bytes_out = recon
            else:
                # Stego may embed directly in scanline bytes as stored inside IDAT.
                scanline_bytes_out = scanline_data
                # prev_scanline is only needed for unfiltering, so keep it unused.

            scanline_idx += 1

            # Append reconstructed bytes until we have enough for brute force.
            need = max_bytes - len(prefix)
            if need > 0:
                prefix += scanline_bytes_out[:need]

            if len(prefix) >= max_bytes:
                return channels, bytes_per_scanline, bytes(prefix)

        if len(prefix) >= max_bytes:
            break

    return channels, bytes_per_scanline, bytes(prefix)


def bits_from_selected_bytes(prefix: bytes, channels: int, channel_selection: tuple[int, ...], bit_pos: int) -> list[int]:
    """
    prefix: interleaved bytes in scanline order, per pixel layout (RGB or RGBA).
    channel_selection: which per-pixel channel indices to use (e.g. (3,) for alpha-only in RGBA).
    """
    bpp = channels
    sel = set(channel_selection)
    bits: list[int] = []
    for i, byte in enumerate(prefix):
        if (i % bpp) in sel:
            bits.append((byte >> bit_pos) & 1)
    return bits


def pack_bits_to_bytes(bits: list[int], offset_bits: int, bit_order: str, out_max_bytes: int) -> bytes:
    """
    bit_order:
      - "msb": first bit in each group of 8 is the MSB of output byte
      - "lsb": first bit in each group of 8 is the LSB of output byte
    """
    if offset_bits >= len(bits):
        return b""

    start = offset_bits
    max_groups = (len(bits) - start) // 8
    max_groups = min(max_groups, out_max_bytes)
    out = bytearray()

    for j in range(max_groups):
        b = 0
        for k in range(8):
            bit = bits[start + j * 8 + k]
            if bit_order == "msb":
                b = (b << 1) | bit
            else:
                b |= (bit << k)
        out.append(b)
    return bytes(out)


def extract_secret_from_prefix(prefix: bytes, channels: int, channel_selection: tuple[int, ...]) -> str | None:
    # We assume message is early in the bitstream; only need limited amount of prefix bytes.
    # Scan up to a few hundred packed bytes (enough to contain secret{XXXXXXXX} even with bit offsets/padding).
    out_max_bytes = 256

    for bit_pos in range(8):
        bits = bits_from_selected_bytes(prefix, channels, channel_selection, bit_pos)
        if len(bits) < 16 * 8:
            continue

        for bit_order in ("msb", "lsb"):
            for offset in range(8):
                packed = pack_bits_to_bytes(bits, offset, bit_order, out_max_bytes)
                if not packed:
                    continue

                m = SECRET_RE.search(packed)
                if m:
                    return m.group(0).decode("ascii", errors="replace")

                # Some embeddings may pack bits in reverse byte order.
                packed_rev = packed[::-1]
                m2 = SECRET_RE.search(packed_rev)
                if m2:
                    return m2.group(0).decode("ascii", errors="replace")

    return None


def channel_selections(channels: int) -> list[tuple[int, ...]]:
    # Start with "most likely" selections; we can expand if needed.
    if channels == 3:
        return [
            (0, 1, 2),
            (0,),
            (1,),
            (2,),
            (0, 1),
            (1, 2),
            (0, 2),
        ]
    # RGBA
    return [
        (0, 1, 2, 3),
        (3,),
        (0,),
        (1,),
        (2,),
        (0, 1, 2),
        (0, 3),
        (1, 3),
        (2, 3),
    ]


def main():
    if len(sys.argv) < 2:
        print("Usage: extract_secrets_from_pngs.py <dir>")
        return 2
    root = sys.argv[1]
    max_source_bytes = 200000
    if len(sys.argv) >= 3:
        max_source_bytes = int(sys.argv[2])
    pngs = sorted([os.path.join(root, p) for p in os.listdir(root) if p.startswith("puzzle_") and p.endswith(".png")])
    if not pngs:
        print("No puzzle_*.png found in", root)
        return 2

    # Decode only a prefix of pixel bytes; should be enough if secrets are early in the embedding.
    # Worst case (alpha-only in RGBA), we still expect enough bits for secret{XXXXXXXX} within this prefix.
    out_lines = []
    for p in pngs:
        rel = os.path.basename(p)
        found = None
        for unfilter in (True, False):
            if found:
                break
            channels, _, prefix = decode_png_pixel_prefix(p, max_source_bytes, unfilter=unfilter)
            for sel in channel_selections(channels):
                found = extract_secret_from_prefix(prefix, channels, sel)
                if found:
                    prefix_mode = "unfiltered" if unfilter else "filtered"
                    out_lines.append(f"{rel}: {found} ({prefix_mode}, sel={sel})")
                    break

        if not found:
            out_lines.append(f"{rel}: NOT_FOUND")
    sys.stdout.write("\n".join(out_lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
