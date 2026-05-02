import os, re, sys, zlib, struct, itertools, math

TOKEN_RE = re.compile(r"secret\{([0-9a-fA-F]{8})\}")

# PNG decoding for 8-bit RGB/RGBA only.

def iter_chunks(png_bytes):
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG")
    pos = 8
    n = len(png_bytes)
    while pos + 8 <= n:
        length = struct.unpack(">I", png_bytes[pos:pos+4])[0]
        ctype = png_bytes[pos+4:pos+8]
        data = png_bytes[pos+8:pos+8+length]
        pos = pos + 8 + length
        crc = png_bytes[pos:pos+4]
        pos += 4
        yield ctype.decode('ascii', 'replace'), data


def paeth_predictor(a, b, c):
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


def decode_png_first_lines(path, max_lines=60):
    with open(path, 'rb') as f:
        png = f.read()

    width = height = bit_depth = color_type = None
    channels = None
    idat_chunks = []
    for ctype, data in iter_chunks(png):
        if ctype == 'IHDR':
            width, height, bit_depth, color_type, _cm, _fm, interlace = struct.unpack(">IIBBBBB", data)
            if interlace != 0 or bit_depth != 8:
                raise ValueError(f"Unsupported PNG: interlace={interlace} bit_depth={bit_depth}")
            if color_type == 2:
                channels = 3
            elif color_type == 6:
                channels = 4
            else:
                raise ValueError(f"Unsupported PNG color_type={color_type}")
        elif ctype == 'IDAT':
            idat_chunks.append(data)
        elif ctype == 'IEND':
            break

    if width is None or channels is None:
        raise ValueError("Missing IHDR")

    lines_to_decode = min(max_lines, height)
    stride = width * channels
    bytes_needed = lines_to_decode * (stride + 1)

    decomp = zlib.decompressobj()
    buf = bytearray()
    for chunk in idat_chunks:
        buf.extend(decomp.decompress(chunk))
        if len(buf) >= bytes_needed:
            break
    if len(buf) < bytes_needed:
        # Fallback: decompress everything (still should be fine for remaining bytes)
        for chunk in idat_chunks:
            if len(buf) >= bytes_needed:
                break
            buf.extend(decomp.decompress(chunk))
        if len(buf) < bytes_needed:
            raise ValueError(f"Could not decode enough data: got {len(buf)}, need {bytes_needed}")

    pixels = bytearray()
    prev = bytearray(stride)
    off = 0
    for _ in range(lines_to_decode):
        ftype = buf[off]
        off += 1
        scan = buf[off:off+stride]
        off += stride
        recon = bytearray(stride)
        if ftype == 0:  # None
            recon[:] = scan
        elif ftype == 1:  # Sub
            for i in range(stride):
                left = recon[i - channels] if i >= channels else 0
                recon[i] = (scan[i] + left) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                recon[i] = (scan[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = recon[i - channels] if i >= channels else 0
                up = prev[i]
                recon[i] = (scan[i] + ((left + up) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                left = recon[i - channels] if i >= channels else 0
                up = prev[i]
                up_left = prev[i - channels] if i >= channels else 0
                recon[i] = (scan[i] + paeth_predictor(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f"Unsupported filter type {ftype}")

        pixels.extend(recon)
        prev = recon

    return width, height, channels, pixels, lines_to_decode


def extract_token_from_pixels(width, height, channels, pixels, n_lines, max_bytes=64):
    num_pixels = width * n_lines
    if num_pixels <= 0:
        return None

    # Generate ordered channel sequences (all non-empty permutations lengths 1..channels)
    idx = list(range(channels))
    channel_seqs = []
    for k in range(1, channels + 1):
        for perm in itertools.permutations(idx, k):
            channel_seqs.append(perm)

    offsets = range(0, 8)
    for ch_seq in channel_seqs:
        k = len(ch_seq)
        # We will potentially skip up to 7 bits, so need offset + 8*max_bytes bits.
        needed_bits = 7 + 8 * max_bytes
        needed_pixels = (needed_bits + k - 1) // k
        if needed_pixels > num_pixels:
            continue

        # For each bit position try to reconstruct bytes.
        for bitpos in range(8):
            # small optimization: pull out just enough bytes for needed_pixels
            # pixels array is interleaved channels_total at each pixel.
            # We'll index directly.
            for endian in ('msb', 'lsb'):
                for offset in offsets:
                    total_bits = offset + 8 * max_bytes
                    # Extract total_bits from bitstream
                    bits = []
                    bits_append = bits.append
                    cnt = 0
                    # Walk pixels until we have enough bits.
                    for p in range(needed_pixels):
                        base = p * channels
                        # Pull bits in the provided channel order
                        for ch in ch_seq:
                            bits_append((pixels[base + ch] >> bitpos) & 1)
                            cnt += 1
                            if cnt >= total_bits:
                                break
                        if cnt >= total_bits:
                            break

                    if len(bits) < total_bits:
                        continue
                    bits = bits[offset:]

                    out = bytearray(max_bytes)
                    for j in range(max_bytes):
                        b0 = bits[j*8:(j+1)*8]
                        if endian == 'msb':
                            val = 0
                            for i, bit in enumerate(b0):
                                val |= (bit & 1) << (7 - i)
                        else:
                            val = 0
                            for i, bit in enumerate(b0):
                                val |= (bit & 1) << i
                        out[j] = val

                    s = out.decode('latin1', errors='ignore')
                    m = TOKEN_RE.search(s)
                    if m:
                        return f"secret{{{m.group(1).lower()}}}"

    return None


def main():
    if len(sys.argv) < 2:
        print("usage: extract_secret.py <image_path>")
        sys.exit(2)
    path = sys.argv[1]
    width, height, channels, pixels, n_lines = decode_png_first_lines(path, max_lines=60)
    # First try on the decoded leading scanlines.
    token = extract_token_from_pixels(width, height, channels, pixels, n_lines, max_bytes=64)
    if token is None:
        # As a fallback, try fewer scanlines but different max_bytes (more conservative).
        token = extract_token_from_pixels(width, height, channels, pixels, n_lines, max_bytes=96)
    print(token or "")

if __name__ == '__main__':
    main()
