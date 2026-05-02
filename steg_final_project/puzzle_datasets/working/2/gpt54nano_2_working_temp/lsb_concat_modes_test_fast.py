import re, importlib.util, itertools
spec=importlib.util.spec_from_file_location('fast_extract','working_temp/fast_extract.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
SECRET_RE=re.compile(rb"secret\{[0-9a-fA-F]{8}\}")

fn='puzzle_0005.png'
# Increase prefix pixels, but not full image
width, rows, channels, raw_prefix = mod.decode_png_prefix(fn, pixels_limit=500000)
raw=raw_prefix
pixel_cap=len(raw)//channels
print('channels',channels,'pixel_cap',pixel_cap,'width',width,'rows',rows)

bit_planes=range(8)
byte_orders=('msb','lsb')
bit_shifts=range(0,16)

orders=list(itertools.permutations(range(channels))) if channels<=4 else [tuple(range(channels))]

for bit_plane in bit_planes:
  for mode in ('interleave','concat'):
    for order in orders:
      k=len(order)
      for byte_order in byte_orders:
        # For each shift, try to decode exactly 16 bytes worth of bits.
        for shift in bit_shifts:
          needed_bits=16*8
          if shift+needed_bits > k*pixel_cap:
            continue
          out=bytearray(16)
          for byte_i in range(16):
            val=0
            for j in range(8):
              bit_index = shift + byte_i*8 + j
              if mode=='interleave':
                pixel_i = bit_index // k
                mi = bit_index % k
                ch = order[mi]
              else:
                pixel_i = bit_index % pixel_cap
                chi = bit_index // pixel_cap
                ch = order[chi]
              b = (raw[pixel_i*channels + ch] >> bit_plane) & 1
              if byte_order=='msb':
                val = (val<<1) | b
              else:
                val |= (b & 1) << j
            out[byte_i]=val
          m=SECRET_RE.search(bytes(out))
          if m:
            print('FOUND',m.group(0).decode(),'bit_plane',bit_plane,'mode',mode,'order',order,'byte_order',byte_order,'shift',shift)
            raise SystemExit

print('no secret found')
