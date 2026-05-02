import os, re, struct, zlib

SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def decode_png_full(path: str):
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            raise ValueError('not png')

        width = height = bit_depth = color_type = None
        channels = None
        interlace = None
        idat_chunks = []

        while True:
            len_bytes = f.read(4)
            if not len_bytes:
                break
            (length,) = struct.unpack('>I', len_bytes)
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

    if channels is None:
        raise ValueError('missing IHDR')

    stride = width * channels
    # decompress all
    raw = zlib.decompress(b''.join(idat_chunks))
    expected = height * (1 + stride)
    if len(raw) < expected:
        raise ValueError(f'raw too short: {len(raw)} < {expected}')
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


def traversal_coords(trav: str, idx: int, width: int, height: int):
    # returns (x,y)
    if trav == 'row':
        y = idx // width
        x = idx - y * width
        return x, y
    if trav == 'row_rev':
        N = width * height
        idx2 = N - 1 - idx
        y = idx2 // width
        x = idx2 - y * width
        return x, y
    if trav == 'col':
        # iterate x blocks, then y
        x = idx // height
        y = idx - x * height
        return x, y
    if trav == 'col_rev':
        N = width * height
        idx2 = N - 1 - idx
        x = idx2 // height
        y = idx2 - x * height
        return x, y
    if trav == 'serp':
        y = idx // width
        x0 = idx - y * width
        if y % 2 == 0:
            x = x0
        else:
            x = (width - 1) - x0
        return x, y
    if trav == 'serp_rev':
        N = width * height
        idx2 = N - 1 - idx
        y = idx2 // width
        x0 = idx2 - y * width
        if y % 2 == 0:
            x = x0
        else:
            x = (width - 1) - x0
        return x, y
    raise ValueError('unknown traversal')


def search_lsb(image_path: str, max_bytes=2048, max_bit_offset=128):
    width, height, channels, pixels = decode_png_full(image_path)
    stride = width * channels
    Npix = width * height

    # Candidate channel orders: for RGB use ordered singles + ordered pairs + ordered triples.
    # For RGBA (channels=4), include singles, ordered pairs, and ordered triples (limit combinatorics).
    channels_list = list(range(channels))

    chan_orders = []
    # singles
    chan_orders += [(c,) for c in channels_list]
    # ordered pairs
    for i in channels_list:
        for j in channels_list:
            if i != j:
                chan_orders.append((i, j))
    # ordered triples/all
    if channels == 3:
        import itertools
        for perm in itertools.permutations(channels_list, 3):
            chan_orders.append(perm)
    elif channels == 4:
        import itertools
        # include all ordered triples, but that is 4P3=24, ok
        for perm in itertools.permutations(channels_list, 3):
            chan_orders.append(perm)
        # also include all 4 in fixed order only and reversed fixed order
        chan_orders.append(tuple(channels_list))
        chan_orders.append(tuple(reversed(channels_list)))

    # De-dupe preserving order
    seen = set()
    chan_orders2 = []
    for co in chan_orders:
        if co not in seen:
            seen.add(co)
            chan_orders2.append(co)

    traversals = ['row', 'row_rev', 'col', 'col_rev', 'serp', 'serp_rev']

    for bit_plane in range(8):
        for trav in traversals:
            for ch_order in chan_orders2:
                k = len(ch_order)
                # total bits available from full image
                total_bits = Npix * k
                need_bits = max_bytes * 8 + max_bit_offset
                if total_bits < need_bits:
                    # limit max_bytes if image small
                    usable_bytes = total_bits // 8
                    if usable_bytes < 16:
                        continue
                    mb = usable_bytes
                else:
                    mb = max_bytes

                # extract bits (up to mb*8 + max_bit_offset)
                bits_needed = mb * 8 + max_bit_offset
                bits = [0] * bits_needed
                for t in range(bits_needed):
                    pix_i = t // k
                    mi = t % k
                    x, y = traversal_coords(trav, pix_i, width, height)
                    ch = ch_order[mi]
                    byte_val = pixels[y * stride + x * channels + ch]
                    bits[t] = (byte_val >> bit_plane) & 1

                for bit_off in range(0, max_bit_offset):
                    # pack bytes from bits[bit_off:]
                    # try msb-first and lsb-first
                    for byte_order in ('msb', 'lsb'):
                        out_len = mb
                        out = bytearray(out_len)
                        for bi in range(out_len):
                            chunk = bits[bit_off + bi*8: bit_off + (bi+1)*8]
                            if len(chunk) < 8:
                                break
                            if byte_order == 'msb':
                                v = 0
                                for b in chunk:
                                    v = (v << 1) | b
                            else:
                                v = 0
                                for j,b in enumerate(chunk):
                                    v |= (b & 1) << j
                            out[bi] = v
                        m = SECRET_RE.search(bytes(out))
                        if m:
                            return m.group(0).decode('ascii'), {
                                'bit_plane': bit_plane,
                                'traversal': trav,
                                'channel_order': ch_order,
                                'bit_offset': bit_off,
                                'byte_order': byte_order,
                                'channels': channels,
                                'width': width,
                                'height': height,
                            }

    return None, None


if __name__ == '__main__':
    # run on a small set
    targets = ['puzzle_0015.png','puzzle_0020.png','puzzle_0021.png','puzzle_0022.png']
    for t in targets:
        if not os.path.exists(t):
            continue
        sec, info = search_lsb(t, max_bytes=2048, max_bit_offset=128)
        print(t, sec, info if sec else '')
