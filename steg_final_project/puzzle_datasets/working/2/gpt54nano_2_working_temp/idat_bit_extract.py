import os, re, struct

SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def read_png_idat_concat(path: str) -> bytes:
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            raise ValueError('not png')
        idat_parts = []
        while True:
            lb = f.read(4)
            if not lb:
                break
            (length,) = struct.unpack('>I', lb)
            ctype = f.read(4)
            data = f.read(length)
            f.read(4)  # crc
            if ctype == b'IDAT':
                idat_parts.append(data)
            elif ctype == b'IEND':
                break
        return b''.join(idat_parts)


def try_extract(idat: bytes, bitpos: int, start: int, stride: int, byte_order: str) -> str | None:
    # Extract 16 bytes => 128 bits
    needed_bytes = 16
    needed_bits = needed_bytes * 8

    # We will take bits from sampled IDAT bytes: idat[start + i*stride]
    # and use bitpos inside each sampled IDAT byte.
    bits = []
    for i in range(needed_bits):
        idx = start + i * stride
        if idx < 0 or idx >= len(idat):
            return None
        bits.append((idat[idx] >> bitpos) & 1)

    out = bytearray(needed_bytes)
    for byte_i in range(needed_bytes):
        chunk = bits[byte_i*8:(byte_i+1)*8]
        if byte_order == 'msb':
            v = 0
            for b in chunk:
                v = (v << 1) | b
        else:  # 'lsb': first bit is bit0
            v = 0
            for j, b in enumerate(chunk):
                v |= (b & 1) << j
        out[byte_i] = v

    m = SECRET_RE.search(bytes(out))
    return m.group(0).decode('ascii') if m else None


def main():
    files = sorted([f for f in os.listdir('.') if f.lower().endswith('.png') and f.startswith('puzzle_')])
    out_path = 'working_temp/idat_bit_hits.txt'
    os.makedirs('working_temp', exist_ok=True)
    open(out_path, 'w').close()

    for fn in files:
        path = os.path.join('.', fn)
        try:
            idat = read_png_idat_concat(path)
        except Exception as e:
            with open(out_path, 'a') as w:
                w.write(f"{fn}\tERROR\t{type(e).__name__}: {e}\n")
            continue

        found = None
        # Search space
        bit_positions = list(range(8))
        start_offsets = list(range(0, 16))
        strides = [1,2,3,4,5,6,7,8,10,12,16,24,32]
        for bitpos in bit_positions:
            for stride in strides:
                for byte_order in ('msb','lsb'):
                    for start in start_offsets:
                        sec = try_extract(idat, bitpos, start, stride, byte_order)
                        if sec:
                            found = (sec, bitpos, start, stride, byte_order)
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break

        with open(out_path, 'a') as w:
            if found:
                sec, bitpos, start, stride, byte_order = found
                w.write(f"{fn}\t{sec}\tbitpos={bitpos}\tstart={start}\tstride={stride}\tbyte_order={byte_order}\n")
                print(fn, sec)
            else:
                w.write(f"{fn}\tNO_SECRET\n")
                print(fn, 'NO_SECRET')


if __name__ == '__main__':
    main()
