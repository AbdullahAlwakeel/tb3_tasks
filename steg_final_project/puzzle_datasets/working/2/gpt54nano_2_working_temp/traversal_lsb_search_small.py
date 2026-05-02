import os, re, struct, zlib, itertools

SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def decode_png_full(path: str):
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            raise ValueError('not png')

        width = height = bit_depth = color_type = interlace = None
        channels = None
        idat_chunks = []

        while True:
            lb = f.read(4)
            if not lb:
                break
            (length,) = struct.unpack('>I', lb)
            ctype = f.read(4)
            data = f.read(length)
            f.read(4)

            if ctype == b'IHDR':
                width, height, bit_depth, color_type, _cm, filter_method, interlace = struct.unpack('>IIBBBBB', data)
                if bit_depth != 8:
                    raise ValueError(f'unsupported bit depth {bit_depth}')
                if interlace != 0:
                    raise ValueError('interlaced not supported')
                if color_type == 2:
                    channels = 3
                elif color_type == 6:
                    channels = 4
                else:
                    raise ValueError(f'unsupported color type {color_type}')
            elif ctype == b'IDAT':
                idat_chunks.append(data)
            elif ctype == b'IEND':
                break

    raw = zlib.decompress(b''.join(idat_chunks))
    stride = width * channels
    expected = height * (1 + stride)
    if len(raw) < expected:
        raw = raw[:expected]

    out_pixels = bytearray(height * stride)
    prev = bytearray(stride)
    in_off = 0
    out_off = 0
    bpp = channels

    for _row in range(height):
        ftype = raw[in_off]
        in_off += 1
        scan = raw[in_off:in_off + stride]
        in_off += stride

        recon = bytearray(stride)
        if ftype == 0:
            recon[:] = scan
        elif ftype == 1:
            for i in range(stride):
                left = recon[i - bpp] if i >= bpp else 0
                recon[i] = (scan[i] + left) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                recon[i] = (scan[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = recon[i - bpp] if i >= bpp else 0
                up = prev[i]
                recon[i] = (scan[i] + ((left + up) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = recon[i - bpp] if i >= bpp else 0
                up = prev[i]
                up_left = prev[i - bpp] if i >= bpp else 0
                p = left + up - up_left
                pa = abs(p - left)
                pb = abs(p - up)
                pc = abs(p - up_left)
                if pa <= pb and pa <= pc:
                    pr = left
                elif pb <= pc:
                    pr = up
                else:
                    pr = up_left
                recon[i] = (scan[i] + pr) & 0xFF
        else:
            raise ValueError(f'unknown filter {ftype}')

        out_pixels[out_off:out_off + stride] = recon
        out_off += stride
        prev = recon

    return width, height, channels, bytes(out_pixels)


def precompute_pix_bases(width: int, height: int, channels: int, traversal: str, needed_pixels: int):
    # returns list pix_base[i] = pixel bytes base offset for traversal index i
    pix_base = [0] * needed_pixels
    for idx in range(needed_pixels):
        if traversal == 'row':
            y = idx // width
            x = idx - y * width
        elif traversal == 'serp':
            y = idx // width
            x0 = idx - y * width
            if y % 2 == 0:
                x = x0
            else:
                x = (width - 1) - x0
        else:
            raise ValueError('bad traversal')
        pix_base[idx] = ((y * width + x) * channels)
    return pix_base


def search_lsb(image_path: str, max_bytes=256, bit_offsets=range(0,8)):
    width, height, channels, pixels = decode_png_full(image_path)
    Npix = width * height

    traversals = ['row','serp']

    # Candidate channel orders: single channels, and full RGB(RGBA) interleaving with permutations.
    ch_indices = list(range(channels))
    chan_orders = [(c,) for c in ch_indices]
    if channels <= 4:
        for perm in itertools.permutations(ch_indices, channels):
            chan_orders.append(perm)

    # Cap bytes if image too small
    results = None

    for traversal in traversals:
        for bit_plane in range(8):
            for ch_order in chan_orders:
                k = len(ch_order)
                pixels_needed = (max_bytes * 8 + max(bit_offsets) + k - 1) // k + 1
                if pixels_needed > Npix:
                    continue
                pix_base = precompute_pix_bases(width, height, channels, traversal, pixels_needed)

                for bit_off in bit_offsets:
                    # assemble exactly max_bytes bytes
                    out = bytearray(max_bytes)
                    for byte_i in range(max_bytes):
                        val = 0
                        for j in range(8):
                            bit_index = bit_off + byte_i * 8 + j
                            pixel_i = bit_index // k
                            mi = bit_index % k
                            ch = ch_order[mi]
                            b = (pixels[pix_base[pixel_i] + ch] >> bit_plane) & 1
                            # pack bits msb-first then optionally reverse for lsb order below
                            val = (val << 1) | b
                        out[byte_i] = val

                    # try msb-first
                    m = SECRET_RE.search(bytes(out))
                    if m:
                        return m.group(0).decode('ascii'), {'bit_plane':bit_plane,'traversal':traversal,'chan_order':ch_order,'bit_off':bit_off,'byte_order':'msb','channels':channels}

                    # try lsb-first by reversing bits inside each byte
                    # Convert by bit-reversing 8 bits.
                    rev_out = bytearray(max_bytes)
                    for i,b in enumerate(out):
                        rb = 0
                        for j in range(8):
                            rb = (rb<<1) | ((b>>j)&1)
                        rev_out[i]=rb
                    m = SECRET_RE.search(bytes(rev_out))
                    if m:
                        return m.group(0).decode('ascii'), {'bit_plane':bit_plane,'traversal':traversal,'chan_order':ch_order,'bit_off':bit_off,'byte_order':'lsb','channels':channels}

    return None, None


if __name__ == '__main__':
    targets = ['puzzle_0015.png','puzzle_0020.png','puzzle_0021.png','puzzle_0022.png','puzzle_0016.png']
    os.makedirs('working_temp', exist_ok=True)
    out_path = 'working_temp/traversal_lsb_small_hits.txt'
    open(out_path,'w').close()

    for t in targets:
        if not os.path.exists(t):
            continue
        sec, info = search_lsb(t, max_bytes=256, bit_offsets=range(0,8))
        if sec:
            print(t, sec, info)
            with open(out_path,'a') as w:
                w.write(f"{t}\t{sec}\t{info}\n")
        else:
            print(t,'NO')
            with open(out_path,'a') as w:
                w.write(f"{t}\tNO\n")
