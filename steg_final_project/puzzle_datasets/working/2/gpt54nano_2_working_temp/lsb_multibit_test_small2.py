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


def precompute_pix_base_row(width, height, channels, needed_pixels):
    pix_base=[0]*needed_pixels
    for idx in range(needed_pixels):
        y = idx // width
        x = idx - y*width
        pix_base[idx]=((y*width + x)*channels)
    return pix_base


def try_config(pixels, width, height, channels, ch_order, bit_width, base_plane, traversal_pix_base, bit_off, byte_order, max_bytes):
    k=len(ch_order)
    bits_per_pixel=k*bit_width
    out=bytearray(max_bytes)
    for byte_i in range(max_bytes):
        val=0
        for j in range(8):
            bit_index = bit_off + byte_i*8 + j
            pix_i = bit_index // bits_per_pixel
            intra = bit_index % bits_per_pixel
            ch_i = intra // bit_width
            bi = intra % bit_width
            ch = ch_order[ch_i]
            plane = base_plane + bi  # low_to_high within the multi-bit segment
            b = (pixels[traversal_pix_base[pix_i] + ch] >> plane) & 1
            if byte_order=='msb':
                val = (val<<1)|b
            else:
                val |= (b&1)<<j
        out[byte_i]=val
    m=SECRET_RE.search(bytes(out))
    return m.group(0).decode('ascii') if m else None


path='puzzle_0015.png'
width,height,channels,pixels=decode_png_full(path)
Npix=width*height
print('img',width,height,'channels',channels,'Npix',Npix)

max_bytes=256
bit_offs=range(0,16)
bit_width=2
base_plane=0
traversal='row'

# Determine pixels_needed for worst bit_off
bits_per_pixel_max = 1*bit_width  # for k=1
pixels_needed = (max(bit_offs) + max_bytes*8 + (channels-1)*0) // (1*bit_width) + 10
# We'll just compute for max k (k=3) too? easiest allocate for worst k=1 => largest pixels_needed
pix_base = precompute_pix_base_row(width,height,channels,pixels_needed)

ch_orders=[]
ch_orders += [(c,) for c in range(channels)]
for perm in itertools.permutations(range(channels), channels):
    ch_orders.append(perm)
# de-dupe
seen=set(); ch_orders2=[]
for co in ch_orders:
    if co not in seen:
        seen.add(co); ch_orders2.append(co)

for ch_order in ch_orders2:
    for bit_off in bit_offs:
        for byte_order in ('msb','lsb'):
            sec=try_config(pixels,width,height,channels,ch_order,bit_width,base_plane,pix_base,bit_off,byte_order,max_bytes)
            if sec:
                print('FOUND',sec,'ch_order',ch_order,'bit_off',bit_off,'byte',byte_order)
                raise SystemExit

print('no secret')
