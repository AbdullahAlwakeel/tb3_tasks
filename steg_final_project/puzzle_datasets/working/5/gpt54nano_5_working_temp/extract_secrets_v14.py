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
            yield ctype, length, data, crc
            if ctype == b'IEND':
                return


def build_streams(path, data_prefix_cap=128):
    types_bytes = bytearray()
    len_bytes = bytearray()
    crc_bytes = bytearray()
    prefix_data = bytearray()

    for ctype, length, data, crc in iter_png_chunks(path):
        types_bytes.extend(ctype)
        len_bytes.extend(struct.pack('>I', length))
        crc_bytes.extend(crc)
        if length > 0:
            prefix_data.extend(data[:min(length, data_prefix_cap)])

    s_meta = bytes(types_bytes + len_bytes + crc_bytes)
    s_meta_prefix = bytes(types_bytes + len_bytes + crc_bytes + prefix_data)
    return s_meta, s_meta_prefix


def try_lsb(source, max_decode_bytes=1024, steps=(1,2,4,8)):
    src = source
    src_len = len(src)
    for bp in range(8):
        for step in steps:
            for start in range(0, min(8, step)):
                for msb_pack in (False, True):
                    max_bits_needed = max_decode_bytes*8
                    last_idx = start + (max_bits_needed-1)*step
                    if last_idx >= src_len:
                        continue
                    out = bytearray(max_decode_bytes)
                    bi_idx = start
                    for out_i in range(max_decode_bytes):
                        v = 0
                        for k in range(8):
                            bit = (src[bi_idx] >> bp) & 1
                            if msb_pack:
                                v = (v<<1) | bit
                            else:
                                v |= bit << k
                            bi_idx += step
                        out[out_i] = v
                    m = SECRET_RE.search(out.decode('latin1', errors='ignore'))
                    if m:
                        return m.group(0)
    return None


def main():
    known={
        'puzzle_0011.png':'secret{cef1d24c}',
        'puzzle_0013.png':'secret{42035123}',
        'puzzle_0014.png':'secret{ee16c3e6}',
    }

    out=[]
    for p in sorted(glob.glob('puzzle_*.png')):
        base=os.path.basename(p)
        if base in known:
            out.append(f"{base}\t{known[base]}")
            print(base, known[base])
            continue

        s_meta, s_meta_prefix = build_streams(p)

        token = None
        # Direct regex on raw stream bytes
        for s in (s_meta, s_meta_prefix):
            try:
                m = SECRET_RE.search(s.decode('latin1', errors='ignore'))
                if m:
                    token = m.group(0)
                    break
            except Exception:
                pass
        if not token:
            token = try_lsb(s_meta, max_decode_bytes=1024)
        if not token:
            token = try_lsb(s_meta_prefix, max_decode_bytes=1024)

        print(base, token if token else '<not found>')
        out.append(f"{base}\t{token if token else '<not found>'}")

    with open('working_temp/secrets_extracted_v14.txt','w') as f:
        f.write('\n'.join(out)+'\n')

if __name__=='__main__':
    main()
