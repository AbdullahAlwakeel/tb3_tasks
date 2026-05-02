import os, re, glob, struct, zlib

SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def iter_chunks(path):
    with open(path,'rb') as f:
        sig=f.read(8)
        if sig!=b'\x89PNG\r\n\x1a\n':
            raise ValueError('not png')
        while True:
            hdr=f.read(8)
            if len(hdr)<8:
                return
            l,ctype=struct.unpack('>I4s',hdr)
            data=f.read(l)
            crc=f.read(4)
            yield ctype,data
            if ctype==b'IEND':
                return


def null_split(b):
    parts=b.split(b'\x00')
    return parts


def extract_from_chunk(ctype,data):
    out=[]
    if ctype==b'tEXt':
        # keyword\0text
        parts=null_split(data)
        if len(parts)>=2:
            out.append(parts[1])
    elif ctype==b'zTXt':
        # keyword\0 compression method (1 byte) + compressed text
        # keyword\0 first
        idx=data.find(b'\x00')
        if idx!=-1 and idx+2<=len(data):
            method=data[idx+1]
            comp=data[idx+2:]
            if method==0:
                try:
                    out.append(zlib.decompress(comp))
                except Exception:
                    pass
    elif ctype==b'iTXt':
        # keyword\0 compression_flag\0 compression_method\0 language_tag\0 translated_keyword\0 text
        # Fields separated by null; compression_flag byte is ASCII '0'/'1'?? Actually byte 0 or 1.
        parts=data.split(b'\x00')
        # Expected: keyword, compression_flag, compression_method, language_tag, translated_keyword, text
        if len(parts)>=6:
            keyword=parts[0]
            compression_flag=parts[1]
            compression_method=parts[2]
            language=parts[3]
            translated=parts[4]
            text=b'\x00'.join(parts[5:])
            if compression_flag[:1]==b'\x00':
                out.append(text)
            else:
                # compressed
                try:
                    if compression_method[:1]==b'\x00':
                        out.append(zlib.decompress(text))
                except Exception:
                    pass
    return out


def main():
    found={}
    for p in sorted(glob.glob('puzzle_*.png')):
        base=os.path.basename(p)
        with open(p,'rb') as f:
            raw=f.read()
        matches=list(SECRET_RE.finditer(raw))
        if matches:
            found[base]=matches[0].group(0).decode('ascii','ignore')
            continue
        # metadata chunks
        for ctype,data in iter_chunks(p):
            if ctype in (b'tEXt',b'zTXt',b'iTXt'):
                chunks=extract_from_chunk(ctype,data)
                for b in chunks:
                    m=SECRET_RE.search(b)
                    if m:
                        found[base]=m.group(0).decode('ascii','ignore')
                        break
            if base in found:
                break

        if base not in found:
            print(base,'<not found>')

    with open('working_temp/secrets_extracted_v17.txt','w') as f:
        for p in sorted(glob.glob('puzzle_*.png')):
            base=os.path.basename(p)
            f.write(f"{base}\t{found.get(base,'<not found>')}\n")

    print('found',len(found))
    for k,v in found.items():
        print(k,v)

if __name__=='__main__':
    main()
