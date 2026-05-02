import re, importlib.util
spec=importlib.util.spec_from_file_location('fast_extract','working_temp/fast_extract.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
SECRET_RE=re.compile(rb"secret\{[0-9a-fA-F]{8}\}")

fn='puzzle_0005.png'
width, rows, channels, raw_prefix = mod.decode_png_prefix(fn, pixels_limit=500000)
raw=raw_prefix
pixel_cap=len(raw)//channels
print('channels',channels,'pixels',pixel_cap,'width',width,'rows',rows)

bit_planes=range(8)
byte_orders=('msb','lsb')

if channels==3:
    channel_orders=[(0,),(1,),(2,),(0,1),(0,2),(1,2),(2,1),(2,0),(1,0),(0,1,2),(0,2,1),(1,0,2),(1,2,0),(2,0,1),(2,1,0)]
else:
    channel_orders=[tuple(range(channels))]


def build_stream(mode, order, bit_plane):
    bits=[]
    if mode=='interleave':
        k=len(order)
        for i in range(pixel_cap):
            base=i*channels
            for ch in order:
                bits.append((raw[base+ch]>>bit_plane)&1)
    else: # concat
        for ch in order:
            for i in range(pixel_cap):
                bits.append((raw[i*channels+ch]>>bit_plane)&1)
    return bits

for bit_plane in bit_planes:
    for mode in ('interleave','concat'):
        for order in channel_orders:
            for byte_order in byte_orders:
                # pack with small bit shifts
                for bit_shift in range(0,16):
                    bits=build_stream(mode, order, bit_plane)
                    if bit_shift>=len(bits):
                        continue
                    bits=bits[bit_shift:]
                    max_bytes=4096
                    bytes_to_take=min(max_bytes, len(bits)//8)
                    if bytes_to_take<16:
                        continue
                    out=bytearray(bytes_to_take)
                    for byte_i in range(bytes_to_take):
                        chunk=bits[byte_i*8:(byte_i+1)*8]
                        if byte_order=='msb':
                            v=0
                            for b in chunk:
                                v=(v<<1)|b
                        else:
                            v=0
                            for j,b in enumerate(chunk):
                                v|=(b&1)<<j
                        out[byte_i]=v
                    m=SECRET_RE.search(bytes(out))
                    if m:
                        print('FOUND',m.group(0).decode(),'bit_plane',bit_plane,'mode',mode,'order',order,'byte',byte_order,'shift',bit_shift)
                        raise SystemExit

print('no secret found')
