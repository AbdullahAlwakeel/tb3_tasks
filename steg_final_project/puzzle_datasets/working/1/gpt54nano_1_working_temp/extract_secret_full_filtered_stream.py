#!/usr/bin/env python3
import os
import re
import struct
import zlib

SECRET_PREFIX = b"secret{"
SECRET_SUFFIX = b"}"
HEX_CHARS = b"0123456789abcdefABCDEF"


def parse_png_inflated(p: str):
    data = open(p, "rb").read()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not png")
    pos = 8
    w = h = bit_depth = color_type = interlace = None
    idat = []
    while pos + 12 <= len(data):
        ln = struct.unpack(">I", data[pos : pos + 4])[0]
        typ = data[pos + 4 : pos + 8]
        cdata = data[pos + 8 : pos + 8 + ln]
        pos += 12 + ln
        pos += 4  # crc
        if typ == b"IHDR":
            w, h, bit_depth, color_type, _cm, _fm, interlace = struct.unpack(
                ">IIBBBBB", cdata
            )
        elif typ == b"IDAT":
            idat.append(cdata)
        elif typ == b"IEND":
            break
    if w is None or h is None:
        raise ValueError("missing IHDR")
    if bit_depth != 8 or interlace != 0:
        raise ValueError(f"unsupported png bit_depth={bit_depth} interlace={interlace}")
    if color_type == 2:
        ch = 3
    elif color_type == 6:
        ch = 4
    else:
        raise ValueError(f"unsupported color_type={color_type}")
    inflated = zlib.decompress(b"".join(idat))
    return w, h, ch, inflated


def filtered_flat(inflated: bytes, w: int, h: int, ch: int) -> bytes:
    stride = w * ch
    per_line = stride + 1
    if len(inflated) < h * per_line:
        raise ValueError("inflated shorter than expected")
    out = bytearray(w * h * ch)
    src_off = 0
    dst_off = 0
    for _y in range(h):
        _ft = inflated[src_off]
        src_off += 1
        out[dst_off : dst_off + stride] = inflated[src_off : src_off + stride]
        src_off += stride
        dst_off += stride
    return bytes(out)


def iter_samples_row(filtered: bytes, w: int, h: int, ch: int, ci_list: list[int]):
    stride = w * ch
    for y in range(h):
        rowoff = y * stride
        for x in range(w):
            base = rowoff + x * ch
            for ci in ci_list:
                yield filtered[base + ci]


def find_secret_stream(samples_iter, k_bits: int, take_end: str, within_order: str) -> str | None:
    # Stream bits -> bytes (MSB-first byte assembly) and search for secret.
    # We assume byte alignment offset is 0 (first produced bit starts at MSB of first byte).
    bits_per_sample = k_bits
    mask = (1 << k_bits) - 1

    def sample_to_val(b: int) -> int:
        if take_end == "lsb":
            return b & mask
        # take_end == "msb"
        return (b >> (8 - k_bits)) & mask

    # rolling 16-byte window
    win = bytearray(16)
    wpos = 0
    filled = 0

    cur = 0
    bitpos = 0  # how many bits currently in cur (0..7), where next bit goes at (7-bitpos)

    for b in samples_iter:
        v = sample_to_val(b)
        if within_order == "msb_first":
            bitseq = range(k_bits - 1, -1, -1)
        else:
            bitseq = range(0, k_bits)

        for i in bitseq:
            bit = (v >> i) & 1
            cur = (cur << 1) | bit
            bitpos += 1
            if bitpos == 8:
                byte = cur & 0xFF
                # update window
                win[wpos] = byte
                wpos = (wpos + 1) % 16
                filled = min(16, filled + 1)

                if filled == 16:
                    # reconstruct current 16-byte window in chronological order
                    # oldest at wpos, newest at wpos-1
                    oldest = wpos
                    cand = bytes(win[oldest:] + win[:oldest])
                    if cand.startswith(SECRET_PREFIX) and cand[-1:] == SECRET_SUFFIX:
                        hex_part = cand[len(SECRET_PREFIX):-1]
                        if len(hex_part) == 8 and all(c in HEX_CHARS for c in hex_part):
                            return "secret{" + hex_part.decode("ascii").lower() + "}"

                # reset for next byte
                cur = 0
                bitpos = 0
    return None


def scan_image(img_path: str) -> str | None:
    w, h, ch, inflated = parse_png_inflated(img_path)
    filt = filtered_flat(inflated, w, h, ch)

    # Likely zsteg-like configs: row-major sample order (xy), and common channel sets.
    if ch == 3:
        ci_sets = [[0, 1, 2]]  # rgb
    else:
        # puzzle_0009 / RGBA
        ci_sets = [[3], [0, 1, 2, 3]]  # a, rgba

    for ci_list in ci_sets:
        for k_bits in (1, 2, 3, 4):
            for take_end in ("lsb", "msb"):
                # heuristic mapping from take_end to within_order
                within_order = "lsb_first" if take_end == "lsb" else "msb_first"
                samples_iter = iter_samples_row(filt, w, h, ch, ci_list)
                sec = find_secret_stream(
                    samples_iter,
                    k_bits=k_bits,
                    take_end=take_end,
                    within_order=within_order,
                )
                if sec:
                    return sec
    return None


def main():
    targets = ["puzzle_0009.png", "puzzle_0010.png", "puzzle_0011.png", "puzzle_0012.png"]
    for t in targets:
        p = os.path.join(".", t)
        sec = scan_image(p)
        print(t, sec or "<NOT_FOUND>")


if __name__ == "__main__":
    main()

