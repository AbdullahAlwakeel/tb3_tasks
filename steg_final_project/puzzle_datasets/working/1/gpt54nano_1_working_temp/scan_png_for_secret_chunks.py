import re, sys, zlib, struct
TOKEN_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")

def iter_chunks(png_bytes):
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a PNG")
    pos = 8
    n = len(png_bytes)
    while pos + 8 <= n:
        length = struct.unpack(">I", png_bytes[pos:pos+4])[0]
        ctype = png_bytes[pos+4:pos+8]
        data = png_bytes[pos+8:pos+8+length]
        pos = pos + 8 + length
        crc = png_bytes[pos:pos+4]
        pos += 4
        yield ctype, data

def try_decompress(data):
    try:
        return zlib.decompress(data)
    except Exception:
        return None

def main():
    for path in sys.argv[1:]:
        data = open(path,'rb').read()
        hits=[]
        for ctype, cdata in iter_chunks(data):
            # raw search
            for m in TOKEN_RE.finditer(cdata):
                hits.append((ctype.decode('ascii','replace'), 'raw', m.group(0)))
            # zTXt: keyword\x00\x00\x01 or method\x00 etc; compressed part begins after 2+keyword+...; we'll just try decompress whole data and then search
            dec = try_decompress(cdata)
            if dec is not None:
                for m in TOKEN_RE.finditer(dec):
                    hits.append((ctype.decode('ascii','replace'), 'zlib', m.group(0)))
        # Also try decompressing the concatenated IDAT stream
        # Find IDAT
        idat=b''
        for ctype,cdata in iter_chunks(data):
            if ctype==b'IDAT':
                idat+=cdata
        try:
            idat_dec = zlib.decompress(idat)
            for m in TOKEN_RE.finditer(idat_dec):
                hits.append(('IDAT','zlib_scan', m.group(0)))
        except Exception:
            pass
        print(path)
        if hits:
            for h in hits[:10]:
                print(' ',h[0],h[1],h[2])
        else:
            print('  no hits')

if __name__=='__main__':
    main()
