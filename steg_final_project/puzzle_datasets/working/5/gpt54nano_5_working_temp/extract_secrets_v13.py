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
    try:
        produced.extend(decomp.flush())
    except Exception:
        pass
    return bytes(produced)


def extract_bits_from_source(src, bp, d, bit_order_in_chunk):
    # returns generator of bits (0/1) as a stream
    # bit_order_in_chunk: 'lsb' means order within d bits is low->high; 'msb' means high->low
    mask = (1 << d) - 1
    if bit_order_in_chunk == 'lsb':
        # appended order: bit0..bit(d-1)
        for b in src:
            v = (b >> bp) & mask
            for k in range(d):
                yield (v >> k) & 1
    else:
        # appended order: bit(d-1)..bit0
        for b in src:
            v = (b >> bp) & mask
            for k in range(d-1, -1, -1):
                yield (v >> k) & 1


def try_decode_secret(src, out_len_bytes=256):
    # step=1, start=0, shiftbits=0..7 (discard bits at start)
    # Try d=1..4 consecutive bits starting at bp_start
    max_total_bits = out_len_bytes * 8

    for d in (1,2,3,4):
        for bp_start in range(0, 8 - d + 1):
            for order_in in ('lsb','msb'):
                # We avoid building full bitstream; just pull bits on demand.
                # We'll implement as pulling from src with an index.

                # Precompute d-bit chunk values per byte to speed: list of int values (0..2^d-1)
                mask=(1<<d)-1
                vals=[(b>>bp_start)&mask for b in src]

                # The bitstream for each byte is derived from vals[i] and order_in
                # We'll implement bit_at(pos): which byte and which bit within that chunk.
                # pos counts bits in appended stream starting at byte0.

                def bit_at(pos):
                    byte_i = pos // d
                    if byte_i >= len(vals):
                        return None
                    within = pos % d
                    v = vals[byte_i]
                    if order_in=='lsb':
                        return (v >> within) & 1
                    else:
                        # msb appended: within=0 corresponds to bit d-1
                        k = d-1-within
                        return (v >> k) & 1

                for shiftbits in range(0, 8):
                    # Need enough bits
                    if shiftbits + max_total_bits > len(vals)*d:
                        continue

                    # Decode out_len_bytes bytes
                    for pack_msb_first in (False, True):
                        decoded = bytearray(out_len_bytes)
                        # pack_msb_first: if True, first bit extracted becomes bit7
                        for bi in range(out_len_bytes):
                            v=0
                            for k in range(8):
                                bit = bit_at(shiftbits + bi*8 + k)
                                if bit is None:
                                    v=None
                                    break
                                if pack_msb_first:
                                    v = (v<<1) | bit
                                else:
                                    v |= bit<<k
                            if v is None:
                                break
                            decoded[bi]=v

                        m = SECRET_RE.search(decoded.decode('latin1', errors='ignore'))
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
            print(base, known[base])
            out.append(f"{base}\t{known[base]}")
            continue

        print('processing', base)
        src = decompress_all_idat(p)
        token = try_decode_secret(src, out_len_bytes=256)
        print(base, token if token else '<not found>')
        out.append(f"{base}\t{token if token else '<not found>'}")

    with open('working_temp/secrets_extracted_v13.txt','w') as f:
        f.write('\n'.join(out)+'\n')

if __name__=='__main__':
    main()
