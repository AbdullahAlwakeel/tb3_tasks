import re, struct, zlib, itertools
SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def decode_png_full(path: str):
    with open(path, 'rb') as f:
        sig=f.read(8)
        if sig!=b'\x89PNG\r\n\x1a\n':
            raise ValueError('not png')
        width=height=bit_depth=color_type=interlace=None
        channels=None
        idat=[]
        while True:
            lb=f.read(4)
            if not lb: break
            l=struct.unpack('>I',lb)[0]
            ctype=f.read(4)
            data=f.read(l)
            f.read(4)
            if ctype==b'IHDR':
                width,height,bit_depth,color_type,_cm,filter_method,interlace=struct.unpack('>IIBBBBB',data)
                if bit_depth!=8 or interlace!=0: raise ValueError('unsupported IHDR')
                if color_type==2: channels=3
                elif color_type==6: channels=4
                else: raise ValueError('ct')
            elif ctype==b'IDAT':
                idat.append(data)
            elif ctype==b'IEND':
                break
    raw=zlib.decompress(b''.join(idat))
    stride=width*channels
    expected=height*(1+stride)
    raw=raw[:expected]

    out=bytearray(height*stride)
    prev=bytearray(stride)
    in_off=0; out_off=0
    bpp=channels
    for _row in range(height):
        ftype=raw[in_off]; in_off+=1
        scan=raw[in_off:in_off+stride]; in_off+=stride
        recon=bytearray(stride)
        if ftype==0:
            recon[:] = scan
        elif ftype==1:
            for i in range(stride):
                left = recon[i-bpp] if i>=bpp else 0
                recon[i]=(scan[i]+left)&0xff
        elif ftype==2:
            for i in range(stride):
                recon[i]=(scan[i]+prev[i])&0xff
        elif ftype==3:
            for i in range(stride):
                left=recon[i-bpp] if i>=bpp else 0
                up=prev[i]
                recon[i]=(scan[i]+((left+up)>>1))&0xff
        elif ftype==4:
            for i in range(stride):
                left=recon[i-bpp] if i>=bpp else 0
                up=prev[i]
                up_left=prev[i-bpp] if i>=bpp else 0
                p=left+up-up_left
                pa=abs(p-left); pb=abs(p-up); pc=abs(p-up_left)
                if pa<=pb and pa<=pc: pr=left
                elif pb<=pc: pr=up
                else: pr=up_left
                recon[i]=(scan[i]+pr)&0xff
        else:
            raise ValueError('filter')
        out[out_off:out_off+stride]=recon
        out_off+=stride
        prev=recon

    return width,height,channels,bytes(out)


def pix_base_for_traversal(width,height,channels,traversal,needed_pixels):
    N=width*height
    pix_base=[0]*needed_pixels
    for idx in range(needed_pixels):
        if traversal=='row':
            pix=idx
        elif traversal=='row_rev':
            pix=N-1-idx
        elif traversal=='serp':
            pix=idx
        elif traversal=='serp_rev':
            pix=N-1-idx
        else:
            raise ValueError
        # map pix->x,y based on serp or row
        if traversal in ('row','row_rev'):
            y=pix//width; x=pix - y*width
        else:
            y=pix//width; x0=pix - y*width
            if y%2==0:
                x=x0
            else:
                x=width-1-x0
        pix_base[idx]=((y*width + x)*channels)
    return pix_base


path='puzzle_0015.png'
width,height,channels,pixels=decode_png_full(path)
Npix=width*height

max_bytes=256
bit_offs=range(0,8)
traversals=['row','row_rev','serp','serp_rev']

chan_orders=[(c,) for c in range(channels)]
for perm in itertools.permutations(range(channels),channels):
    chan_orders.append(perm)

# allocate max pixels needed for k=1
pixels_needed = (max(bit_offs) + max_bytes*8 + 1)//1 + 2

for trav in traversals:
    pix_base=pix_base_for_traversal(width,height,channels,trav,pixels_needed)
    for bit_plane in range(8):
        for ch_order in chan_orders:
            k=len(ch_order)
            # pixels needed depends on k but pix_base computed for k=1; still enough since k>=1
            for bit_off in bit_offs:
                out=bytearray(max_bytes)
                for byte_i in range(max_bytes):
                    v=0
                    for j in range(8):
                        bit_index=bit_off + byte_i*8 + j
                        pix_i=bit_index//k
                        mi=bit_index%k
                        ch=ch_order[mi]
                        b=(pixels[pix_base[pix_i]+ch]>>bit_plane)&1
                        v=(v<<1)|b
                    out[byte_i]=v
                m=SECRET_RE.search(bytes(out))
                if m:
                    print('FOUND',m.group(0).decode(),'trav',trav,'bit_plane',bit_plane,'ch_order',ch_order,'bit_off',bit_off,'byte_order','msb')
                    raise SystemExit
                # byte_order lsb (reverse bits inside each byte)
                rev=bytearray(max_bytes)
                for i,b in enumerate(out):
                    rb=0
                    for j in range(8):
                        rb=(rb<<1)|((b>>j)&1)
                    rev[i]=rb
                m=SECRET_RE.search(bytes(rev))
                if m:
                    print('FOUND',m.group(0).decode(),'trav',trav,'bit_plane',bit_plane,'ch_order',ch_order,'bit_off',bit_off,'byte_order','lsb')
                    raise SystemExit

print('no secret')
