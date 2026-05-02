import re, struct, zlib, itertools

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
                    raise ValueError('bit depth')
                if interlace != 0:
                    raise ValueError('interlace')
                if color_type == 2:
                    channels = 3
                elif color_type == 6:
                    channels = 4
                else:
                    raise ValueError('ct')
            elif ctype == b'IDAT':
                idat_chunks.append(data)
            elif ctype == b'IEND':
                break

    raw = zlib.decompress(b''.join(idat_chunks))
    stride = width * channels
    expected = height * (1 + stride)
    raw = raw[:expected]

    out_pixels = bytearray(height * stride)
    prev = bytearray(stride)
    in_off = 0
    out_off = 0
    bpp = channels

    for _row in range(height):
        ftype = raw[in_off]
        in_off += 1
        scan = raw[in_off:in_off+stride]
        in_off += stride

        recon = bytearray(stride)
        if ftype == 0:
            recon[:] = scan
        elif ftype == 1:
            for i in range(stride):
                left = recon[i-bpp] if i>=bpp else 0
                recon[i] = (scan[i] + left) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                recon[i] = (scan[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = recon[i-bpp] if i>=bpp else 0
                up = prev[i]
                recon[i] = (scan[i] + ((left+up)>>1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = recon[i-bpp] if i>=bpp else 0
                up = prev[i]
                up_left = prev[i-bpp] if i>=bpp else 0
                p = left + up - up_left
                pa = abs(p-left); pb=abs(p-up); pc=abs(p-up_left)
                if pa <= pb and pa <= pc:
                    pr=left
                elif pb <= pc:
                    pr=up
                else:
                    pr=up_left
                recon[i] = (scan[i] + pr) & 0xFF
        else:
            raise ValueError('filter')

        out_pixels[out_off:out_off+stride]=recon
        out_off += stride
        prev = recon

    return width, height, channels, bytes(out_pixels)


def precompute_pix_base(width, height, channels, traversal, needed_pixels):
    pix_base = [0]*needed_pixels
    for idx in range(needed_pixels):
        if traversal=='row':
            y = idx // width
            x = idx - y*width
        elif traversal=='serp':
            y = idx // width
            x0 = idx - y*width
            x = x0 if (y%2==0) else (width-1-x0)
        else:
            raise ValueError('trav')
        pix_base[idx] = ((y*width + x)*channels)
    return pix_base


def search_multibit(path, max_bytes=512, bit_off_max=15, bit_widths=(1,2,3)):
    width, height, channels, pixels = decode_png_full(path)
    Npix = width*height

    ch_orders=[]
    ch_orders += [(c,) for c in range(channels)]
    for perm in itertools.permutations(range(channels)):
        if len(perm)==channels:
            ch_orders.append(perm)

    traversals=['row','serp']

    for traversal in traversals:
        for ch_order in ch_orders:
            k=len(ch_order)
            for bit_width in bit_widths:
                bits_per_pixel=k*bit_width
                for base_plane in range(0, 8-bit_width+1):
                    # compute needed_pixels for worst offset
                    pixels_needed = (bit_off_max + max_bytes*8 + bits_per_pixel -1)//bits_per_pixel + 1
                    if pixels_needed> Npix:
                        continue
                    pix_base = precompute_pix_base(width,height,channels,traversal,pixels_needed)

                    for bit_off in range(0, bit_off_max+1):
                        for segment_order in ('low_to_high','high_to_low'):
                            for byte_order in ('msb','lsb'):
                                out=bytearray(max_bytes)
                                for byte_i in range(max_bytes):
                                    v=0
                                    for j in range(8):
                                        bit_index = bit_off + byte_i*8 + j
                                        pix_i = bit_index // bits_per_pixel
                                        intra = bit_index % bits_per_pixel
                                        ch_i = intra // bit_width
                                        bi = intra % bit_width
                                        ch = ch_order[ch_i]
                                        if segment_order=='low_to_high':
                                            plane = base_plane + bi
                                        else:
                                            plane = base_plane + (bit_width-1 - bi)
                                        b = (pixels[pix_base[pix_i]+ch] >> plane) & 1
                                        if byte_order=='msb':
                                            v = (v<<1)|b
                                        else:
                                            # for lsb packing, first extracted bit is bit0
                                            v |= (b&1)<<j
                                    out[byte_i]=v

                                m=SECRET_RE.search(bytes(out))
                                if m:
                                    return m.group(0).decode(),{
                                        'traversal':traversal,'ch_order':ch_order,'bit_width':bit_width,'base_plane':base_plane,
                                        'segment_order':segment_order,'bit_off':bit_off,'byte_order':byte_order
                                    }
    return None,None


sec, info = search_multibit('puzzle_0015.png', max_bytes=256, bit_off_max=15)
print('RESULT',sec,info)
