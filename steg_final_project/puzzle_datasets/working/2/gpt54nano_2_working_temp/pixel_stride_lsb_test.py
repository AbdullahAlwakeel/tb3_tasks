import re, struct, zlib
SECRET_RE=re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def decode_png_full(path):
    with open(path,'rb') as f:
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
                if bit_depth!=8 or interlace!=0: raise ValueError('IHDR')
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
                left=recon[i-bpp] if i>=bpp else 0
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


def main():
    path='puzzle_0015.png'
    width,height,channels,pixels=decode_png_full(path)
    Npix=width*height
    stride_values=[1,2,3,4,5,8,16]
    max_bytes=256
    bit_offs=range(0,8)

    for ch in range(channels):
        for bit_plane in range(8):
            for stride in stride_values:
                # Need bits_needed = max_bytes*8 + max(bit_off)
                bits_needed=max_bytes*8 + max(bit_offs)
                max_pix_index = (bits_needed-1)*stride
                if max_pix_index>=Npix:
                    continue
                for bit_off in bit_offs:
                    out=bytearray(max_bytes)
                    for byte_i in range(max_bytes):
                        v=0
                        for j in range(8):
                            bit_index=bit_off + byte_i*8 + j
                            pix_i=bit_index*stride
                            b=(pixels[pix_i*channels + ch] >> bit_plane) & 1
                            v=(v<<1)|b
                        out[byte_i]=v
                    if SECRET_RE.search(bytes(out)):
                        print('FOUND',SECRET_RE.search(bytes(out)).group(0).decode(),'ch',ch,'bit_plane',bit_plane,'stride',stride,'bit_off',bit_off,'byte_order','msb')
                        return
                    # lsb byte order: reverse bits within each byte
                    rev=bytearray(max_bytes)
                    for i,bv in enumerate(out):
                        rb=0
                        for j in range(8):
                            rb=(rb<<1)|((bv>>j)&1)
                        rev[i]=rb
                    m=SECRET_RE.search(bytes(rev))
                    if m:
                        print('FOUND',m.group(0).decode(),'ch',ch,'bit_plane',bit_plane,'stride',stride,'bit_off',bit_off,'byte_order','lsb')
                        return

    print('no secret')


if __name__=='__main__':
    main()
