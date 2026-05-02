import os, re, struct, zlib, sys

pattern = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def read_chunks(path):
    with open(path, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            return []
        chunks = []
        while True:
            len_bytes = f.read(4)
            if not len_bytes:
                break
            (length,) = struct.unpack('>I', len_bytes)
            ctype = f.read(4)
            data = f.read(length)
            crc = f.read(4)
            chunks.append((ctype, data))
            if ctype == b'IEND':
                break
        return chunks


def extract_text_from_chunk(ctype, data):
    out = []
    try:
        if ctype == b'tEXt':
            parts = data.split(b'\x00', 1)
            if len(parts) == 2:
                out.append(parts[1])
        elif ctype == b'zTXt':
            # keyword\0 compression_method(1) + compressed_text
            parts = data.split(b'\x00', 1)
            if len(parts) == 2 and len(parts[1]) >= 1:
                comp_method = parts[1][0:1]
                comp = parts[1][1:]
                # comp_method usually 0
                try:
                    txt = zlib.decompress(comp)
                    out.append(txt)
                except Exception:
                    pass
        elif ctype == b'iTXt':
            # keyword\0 compression_flag(1) compression_method\0 lang_tag\0 translated_keyword\0 text
            # We'll do a permissive parse.
            # keyword\0
            parts = data.split(b'\x00', 1)
            if len(parts) != 2:
                return out
            rest = parts[1]
            if len(rest) < 2:
                return out
            comp_flag = rest[0:1]
            rest2 = rest[1:]
            # compression_method\0
            mparts = rest2.split(b'\x00', 1)
            if len(mparts) != 2:
                return out
            rest3 = mparts[1]
            # lang_tag\0 translated_keyword\0
            lang_split = rest3.split(b'\x00', 1)
            if len(lang_split) != 2:
                return out
            rest4 = lang_split[1]
            trans_split = rest4.split(b'\x00', 1)
            if len(trans_split) != 2:
                return out
            text_blob = trans_split[1]
            if comp_flag == b'\x00':
                out.append(text_blob)
            else:
                # compressed
                try:
                    out.append(zlib.decompress(text_blob))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def scan_file(path):
    chunks = read_chunks(path)
    hits = []
    for ctype, data in chunks:
        # Search in raw chunk data too (sometimes payload is stored in custom chunks)
        for m in pattern.finditer(data):
            hits.append((ctype, 'raw', m.group(0)))
        for blob in extract_text_from_chunk(ctype, data):
            for m in pattern.finditer(blob):
                hits.append((ctype, 'text', m.group(0)))
    return hits


if __name__ == '__main__':
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    for fn in sorted(os.listdir(root)):
        if not fn.lower().endswith('.png'):
            continue
        path = os.path.join(root, fn)
        hits = scan_file(path)
        if hits:
            for ctype, kind, secret in hits[:20]:
                print(f"{fn}\t{ctype.decode('ascii','replace')}\t{kind}\t{secret.decode()}\t{len(secret)}")
