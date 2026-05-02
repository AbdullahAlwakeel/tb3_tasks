import os, re, glob, struct, zlib

SECRET_RE = re.compile(r"secret\{[0-9a-fA-F]{8}\}")


def iter_png_chunks(path):
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            raise ValueError('not png')
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                return
            length, ctype = struct.unpack('>I4s', hdr)
            data = f.read(length)
            crc = f.read(4)
            yield ctype, data, crc
            if ctype == b'IEND':
                return


def decompress_all_idat(path):
    decomp = zlib.decompressobj()
    produced = bytearray()
    for ctype, data, _crc in iter_png_chunks(path):
        if ctype == b'IDAT':
            produced.extend(decomp.decompress(data))
    # flush any remaining
    try:
        produced.extend(decomp.flush())
    except Exception:
        pass
    return bytes(produced)


def try_source(source_bytes, max_decode_bytes=2048, bitplane_range=range(8), steps=(1,2,4,8), pack_msb_first=(False,True)):
    # Returns token or None
    src = source_bytes
    src_len = len(src)
    # We will extract bits at positions: idx = start_offset + bit_index*step
    # Then pack into bytes in the output bitstream.
    max_bits_needed = max_decode_bytes * 8

    for bp in bitplane_range:
        for step in steps:
            for start in range(0, min(8, step)):
                for msb_pack in pack_msb_first:
                    # Quick bound check
                    max_bit_index = max_bits_needed - 1
                    last_idx = start + max_bit_index * step
                    if last_idx >= src_len:
                        # Not enough bits
                        continue

                    # Decode
                    out = bytearray(max_decode_bytes)
                    bit_i = start
                    for bi in range(max_decode_bytes):
                        v = 0
                        for k in range(8):
                            bit = (src[bit_i] >> bp) & 1
                            if msb_pack:
                                v = (v << 1) | bit
                            else:
                                v |= bit << k
                            bit_i += step
                        out[bi] = v

                    s = out.decode('latin1', errors='ignore')
                    m = SECRET_RE.search(s)
                    if m:
                        return m.group(0)

    return None


def main():
    out=[]
    # Pre-known from earlier attempts
    known = {
        'puzzle_0011.png': 'secret{cef1d24c}',
        'puzzle_0013.png': 'secret{42035123}',
        'puzzle_0014.png': 'secret{ee16c3e6}',
    }

    for p in sorted(glob.glob('puzzle_*.png')):
        base=os.path.basename(p)
        if base in known:
            print(base, known[base])
            out.append(f"{base}\t{known[base]}")
            continue

        print('processing', base)
        file_bytes = open(p,'rb').read()
        idat = decompress_all_idat(p)

        token = None
        # Try IDAT first (more likely)
        token = try_source(idat, max_decode_bytes=2048, steps=(1,2,4,8))
        if not token:
            token = try_source(file_bytes, max_decode_bytes=2048, steps=(1,2,4,8))

        print(base, token if token else '<not found>')
        out.append(f"{base}\t{token if token else '<not found>'}")

    with open('working_temp/secrets_extracted_v12.txt','w') as f:
        f.write('\n'.join(out)+'\n')

if __name__=='__main__':
    main()
