#!/usr/bin/env python3
import os
import re
import struct
import zlib
from dataclasses import dataclass
from typing import List, Optional, Tuple, Iterable
import itertools


SECRET_RE = re.compile(r"^secret\{[0-9a-fA-F]{8}\}$")
SECRET_LEN_BYTES = 16  # "secret{" (7) + 8 hex + "}" (1) = 16 bytes
SECRET_BITS = SECRET_LEN_BYTES * 8


BITREV_TABLE = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))

SECRET_PREFIX = b"secret{"


@dataclass(frozen=True)
class PNGInfo:
    width: int
    height: int
    color_type: int
    bit_depth: int
    bpp: int  # bytes per pixel


def _read_chunks_and_ihdr(path: str) -> Tuple[PNGInfo, List[bytes]]:
    with open(path, "rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"{path}: not a PNG")

        ihdr = None
        idat_datas: List[bytes] = []

        while True:
            raw_len = f.read(4)
            if len(raw_len) != 4:
                raise ValueError(f"{path}: truncated")
            length = struct.unpack(">I", raw_len)[0]
            ctype = f.read(4)
            if len(ctype) != 4:
                raise ValueError(f"{path}: truncated chunk type")
            data = f.read(length)
            if len(data) != length:
                raise ValueError(f"{path}: truncated chunk data")
            _crc = f.read(4)

            if ctype == b"IHDR":
                if length != 13:
                    raise ValueError(f"{path}: unexpected IHDR length {length}")
                width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                    ">IIBBBBB", data
                )
                if comp != 0 or filt != 0:
                    raise ValueError(f"{path}: unsupported PNG compression/filter method")
                if interlace != 0:
                    raise ValueError(f"{path}: unsupported interlace={interlace}")
                if bit_depth != 8:
                    raise ValueError(f"{path}: unsupported bit_depth={bit_depth}")
                if color_type == 2:
                    bpp = 3
                elif color_type == 6:
                    bpp = 4
                else:
                    raise ValueError(f"{path}: unsupported color_type={color_type}")
                ihdr = PNGInfo(
                    width=width,
                    height=height,
                    color_type=color_type,
                    bit_depth=bit_depth,
                    bpp=bpp,
                )
            elif ctype == b"IDAT":
                idat_datas.append(data)
            elif ctype == b"IEND":
                break

        if ihdr is None:
            raise ValueError(f"{path}: missing IHDR")

        return ihdr, idat_datas


def _paeth_predictor(a: int, b: int, c: int) -> int:
    # a = left, b = up, c = up-left
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_8bit_rgb_rgba(path: str) -> Tuple[PNGInfo, bytes]:
    info, _ = _read_chunks_and_ihdr(path)
    return decode_png_8bit_rgb_rgba_prefix(path, pixels_needed=info.width * info.height)


def decode_png_8bit_rgb_rgba_prefix(path: str, *, pixels_needed: int) -> Tuple[PNGInfo, bytes]:
    """
    Decode only the first `pixels_needed` pixels (row-major) into raw pixel byte stream.
    This keeps the unfiltering work bounded and is enough for typical LSB stego where
    the payload is stored near the beginning of the bitstream.
    """
    info, idat_datas = _read_chunks_and_ihdr(path)

    pixels_needed = max(0, min(pixels_needed, info.width * info.height))
    stride = info.width * info.bpp
    row_bytes = stride + 1  # +1 filter byte

    rows_needed = (pixels_needed + info.width - 1) // info.width  # ceil
    # If pixels_needed == 0, decode nothing.
    if rows_needed == 0:
        return info, b""

    needed_uncompressed_len = rows_needed * row_bytes

    # Stream zlib decompression until we have enough uncompressed bytes.
    d = zlib.decompressobj()
    uncompressed_parts: List[bytes] = []
    got = 0
    for chunk in idat_datas:
        if got >= needed_uncompressed_len:
            break
        part = d.decompress(chunk)
        if part:
            uncompressed_parts.append(part)
            got += len(part)
    uncompressed = b"".join(uncompressed_parts)
    if len(uncompressed) < needed_uncompressed_len:
        # Best effort: fall back to what we got (decoder will fail if too short).
        needed_uncompressed_len = len(uncompressed)

    uncompressed = uncompressed[:needed_uncompressed_len]

    # Unfilter only the decoded rows.
    out = bytearray(rows_needed * stride)
    prev_row = bytearray(stride)
    cur_row = bytearray(stride)

    offset = 0
    for y in range(rows_needed):
        filter_type = uncompressed[offset]
        offset += 1
        filtered = uncompressed[offset : offset + stride]
        offset += stride

        if filter_type == 0:  # None
            cur_row[:] = filtered
        elif filter_type == 1:  # Sub
            for x in range(stride):
                left = cur_row[x - info.bpp] if x >= info.bpp else 0
                cur_row[x] = (filtered[x] + left) & 0xFF
        elif filter_type == 2:  # Up
            for x in range(stride):
                cur_row[x] = (filtered[x] + prev_row[x]) & 0xFF
        elif filter_type == 3:  # Average
            for x in range(stride):
                left = cur_row[x - info.bpp] if x >= info.bpp else 0
                up = prev_row[x]
                cur_row[x] = (filtered[x] + ((left + up) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for x in range(stride):
                left = cur_row[x - info.bpp] if x >= info.bpp else 0
                up = prev_row[x]
                up_left = prev_row[x - info.bpp] if x >= info.bpp else 0
                cur_row[x] = (filtered[x] + _paeth_predictor(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f"{path}: unsupported filter_type={filter_type}")

        out[y * stride : (y + 1) * stride] = cur_row
        prev_row, cur_row = cur_row, prev_row  # swap buffers

    # Trim to exactly `pixels_needed` pixels.
    out_bytes = out[: pixels_needed * info.bpp]
    return info, bytes(out_bytes)


def decode_png_filtered_prefix(path: str, *, pixels_needed: int) -> Tuple[PNGInfo, bytes]:
    """
    Decode only the first `pixels_needed` pixels and return the *filtered*
    scanline bytes as stored in the PNG stream (i.e., after zlib inflate but
    before applying the PNG unfilter reconstruction).
    """
    info, idat_datas = _read_chunks_and_ihdr(path)

    pixels_needed = max(0, min(pixels_needed, info.width * info.height))
    stride = info.width * info.bpp
    row_bytes = stride + 1  # +1 filter byte per row

    rows_needed = (pixels_needed + info.width - 1) // info.width  # ceil
    if rows_needed == 0:
        return info, b""

    needed_uncompressed_len = rows_needed * row_bytes

    d = zlib.decompressobj()
    uncompressed_parts: List[bytes] = []
    got = 0
    for chunk in idat_datas:
        if got >= needed_uncompressed_len:
            break
        part = d.decompress(chunk)
        if part:
            uncompressed_parts.append(part)
            got += len(part)

    uncompressed = b"".join(uncompressed_parts)[:needed_uncompressed_len]

    out = bytearray(pixels_needed * info.bpp)
    out_off = 0
    for y in range(rows_needed):
        row_start = y * row_bytes
        filtered_row = uncompressed[row_start + 1 : row_start + 1 + stride]
        remaining_pixels = min(info.width, pixels_needed - y * info.width)
        take_len = remaining_pixels * info.bpp
        out[out_off : out_off + take_len] = filtered_row[:take_len]
        out_off += take_len

    return info, bytes(out)


def try_decode_secret_from_samples(
    samples: bytes,
    *,
    max_offset_bits: int,
    bitplane: int,
    invert: bool,
    msb_first: bool,
    step: int = 1,
) -> Optional[str]:
    step = max(1, step)

    # Access samples at indices: off + (SECRET_BITS-1)*step, where off in [0..max_offset_bits].
    max_index = max_offset_bits + (SECRET_BITS - 1) * step
    need = max_index + 1
    if len(samples) < need:
        need = len(samples)
    if need < SECRET_BITS * step:
        # Not enough data to cover a full 128-bit payload even with off=0.
        return None

    # Extract candidate bits once for this bitplane.
    bits = [(b >> bitplane) & 1 for b in samples[:need]]
    if invert:
        bits = [1 - x for x in bits]

    # Max off such that we can still index all bits required for the 128-bit payload.
    max_off = min(max_offset_bits, (len(bits) - 1 - (SECRET_BITS - 1) * step))
    if max_off < 0:
        return None

    # Precompute nibble-swap table for bytes.
    if not hasattr(try_decode_secret_from_samples, "_nibswap"):
        try_decode_secret_from_samples._nibswap = bytes(
            ((i >> 4) | ((i & 0x0F) << 4)) for i in range(256)
        )
    nibswap = try_decode_secret_from_samples._nibswap

    def decode_raw_byte(off_: int, byte_index: int) -> int:
        """
        Decode one raw byte (0..15) from bits.
        byte_index=0 corresponds to secret's first byte ("s").
        """
        base = off_ + byte_index * 8 * step
        val = 0
        if msb_first:
            # Within the byte: first encountered bit is MSB.
            for k in range(8):
                val = (val << 1) | bits[base + k * step]
        else:
            # Within the byte: first encountered bit is LSB.
            for k in range(8):
                val |= bits[base + k * step] << k
        return val

    prefix_len = len(SECRET_PREFIX)
    for off in range(max_off + 1):
        # Decode prefix bytes (e.g., "secret{") for fast rejection.
        raw_prefix = bytearray(prefix_len)
        for j in range(prefix_len):
            raw_prefix[j] = decode_raw_byte(off, j)

        # Check 3 non-reversing variants (positions unchanged).
        for variant in ("raw", "nibswap", "bitrev"):
            if variant == "raw":
                pref = bytes(raw_prefix)
            elif variant == "nibswap":
                pref = bytes(nibswap[b] for b in raw_prefix)
            else:  # bitrev
                pref = bytes(BITREV_TABLE[b] for b in raw_prefix)

            xor_key = pref[0] ^ SECRET_PREFIX[0]  # infer XOR key if used
            # XOR-check prefix without decoding full payload yet.
            ok = True
            for i in range(prefix_len):
                if (pref[i] ^ xor_key) != SECRET_PREFIX[i]:
                    ok = False
                    break
            if not ok:
                continue

            # Prefix matched => decode remaining bytes to verify full regex.
            raw_full = bytearray(SECRET_LEN_BYTES)
            raw_full[:prefix_len] = raw_prefix
            for j in range(prefix_len, SECRET_LEN_BYTES):
                raw_full[j] = decode_raw_byte(off, j)
            raw_full = bytes(raw_full)

            if variant == "raw":
                cand_full = raw_full
            elif variant == "nibswap":
                cand_full = bytes(nibswap[b] for b in raw_full)
            else:
                cand_full = bytes(BITREV_TABLE[b] for b in raw_full)

            cand_decoded = bytes((b ^ xor_key) for b in cand_full)
            try:
                s = cand_decoded.decode("ascii")
            except UnicodeDecodeError:
                continue
            if SECRET_RE.match(s):
                return s

        # Check reverse-byte-order variant (raw_full[::-1]).
        # Candidate prefix in reversed variant equals raw bytes 15..8 => reverse of raw_suffix.
        raw_suffix_prefix = bytearray(8)
        for j in range(8):
            raw_suffix_prefix[j] = decode_raw_byte(off, 8 + j)

        # reversed candidate prefix: raw_suffix_prefix[7]..raw_suffix_prefix[0]
        rev_pref = bytes(reversed(raw_suffix_prefix))[:prefix_len]
        xor_key = rev_pref[0] ^ SECRET_PREFIX[0]
        ok = True
        for i in range(prefix_len):
            if (rev_pref[i] ^ xor_key) != SECRET_PREFIX[i]:
                ok = False
                break
        if ok:
            raw_full = bytearray(SECRET_LEN_BYTES)
            raw_full[:prefix_len] = raw_prefix
            for j in range(prefix_len, SECRET_LEN_BYTES):
                raw_full[j] = decode_raw_byte(off, j)
            cand_full = bytes(raw_full)[::-1]
            cand_decoded = bytes((b ^ xor_key) for b in cand_full)
            try:
                s = cand_decoded.decode("ascii")
            except UnicodeDecodeError:
                continue
            if SECRET_RE.match(s):
                return s

    return None


def extract_secret_for_image(path: str, max_offset_bits: int = 64, *, steps: Iterable[int] = (1, 2, 3, 4)) -> Optional[str]:
    # We only need enough samples to cover the maximum offset and the 16-byte payload.
    # For channel-sliced sampling, each pixel contributes 1 sample byte; for the ALL mode
    # we only need the first few pixels anyway.
    max_step = max(steps) if steps else 1
    # For bit sampling with step>1 we need indices up to:
    # off + (SECRET_BITS-1)*step, where off in [0..max_offset_bits]
    pixels_needed = max_offset_bits + (SECRET_BITS - 1) * max_step + 1  # channel modes need this many samples

    # Try both:
    # - unfiltered pixel values (after PNG unfilter reconstruction)
    # - filtered scanline bytes (immediately after zlib inflate, before unfilter)
    for _decoder_name, decode_fn in [
        ("unfiltered", decode_png_8bit_rgb_rgba_prefix),
        ("filtered", decode_png_filtered_prefix),
    ]:
        info, raw = decode_fn(path, pixels_needed=pixels_needed)

        # Candidate "sample sequences":
        # - each single channel (byte positions within each pixel)
        # - all channels sequentially
        candidates: List[Tuple[str, bytes]] = []
        if info.color_type == 2:
            channel_names = ["R", "G", "B"]
            channel_idx = [0, 1, 2]
        elif info.color_type == 6:
            channel_names = ["R", "G", "B", "A"]
            channel_idx = [0, 1, 2, 3]
        else:
            raise ValueError(f"unexpected color_type={info.color_type}")

        for name, c in zip(channel_names, channel_idx):
            candidates.append((name, raw[c::info.bpp]))
        candidates.append(("ALL", raw))  # byte order in the pixel stream

        # Also try permutations of channel order within each pixel.
        # Some stego encoders serialize bytes in a permuted channel order.
        if info.bpp == 3:
            perms = list(itertools.permutations(range(3)))
            base_channels = [raw[i::3] for i in range(3)]  # each length = n_pixels
            n_pixels = len(raw) // 3
            for perm in perms:
                # Already covered the canonical (0,1,2) ordering.
                if perm == (0, 1, 2):
                    continue
                samples_perm = bytearray(len(raw))
                for pi in range(n_pixels):
                    for k, c in enumerate(perm):
                        samples_perm[pi * 3 + k] = base_channels[c][pi]
                perm_name = f"PERM{perm[0]}{perm[1]}{perm[2]}"
                candidates.append((perm_name, bytes(samples_perm)))

        # Brute common LSB-like variants.
        for _samp_name, samples in candidates:
            # Ensure enough samples for the largest step.
            if len(samples) < (max_offset_bits + (SECRET_BITS - 1) * max_step + 1):
                continue
            for bitplane in range(8):
                for msb_first in (True, False):
                    for invert in (False, True):
                        for step in steps:
                            res = try_decode_secret_from_samples(
                                samples,
                                max_offset_bits=max_offset_bits,
                                bitplane=bitplane,
                                invert=invert,
                                msb_first=msb_first,
                                step=step,
                            )
                            if res is not None:
                                return res

    return None


def main():
    cwd = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(cwd, ".."))
    img_dir = repo_root  # current cwd is repo_root when script invoked from working_temp
    images = sorted(
        [os.path.join(img_dir, f) for f in os.listdir(img_dir) if f.startswith("puzzle_") and f.endswith(".png")]
    )

    os.makedirs(os.path.join(cwd, "outputs"), exist_ok=True)

    results_path = os.path.join(cwd, "secrets_found.txt")
    with open(results_path, "w", encoding="utf-8") as out:
        for img_path in images:
            secret = extract_secret_for_image(img_path, max_offset_bits=512)
            if secret is None:
                out.write(f"{os.path.basename(img_path)}: MISSING\n")
            else:
                out.write(f"{os.path.basename(img_path)}: {secret}\n")
            out.flush()

    print(f"Wrote: {results_path}")


if __name__ == "__main__":
    main()
