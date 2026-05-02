from pathlib import Path
from PIL import Image
import base64
import itertools
import numpy as np
import re

secret_re = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")
base64_prefix = b"c2VjcmV0"

files = [
    Path("puzzle_0006.png"),
    Path("puzzle_0012.png"),
]

orders = [
    ("rgb", ("R", "G", "B")),
    ("bgr", ("B", "G", "R")),
    ("r", ("R",)),
    ("g", ("G",)),
    ("b", ("B",)),
]

dirs = [
    "xy",
    "yx",
]

def pack_bits(bits, bitorder):
    n = (len(bits) // 8) * 8
    if n < 8:
        return b""
    mat = bits[:n].reshape(-1, 8)
    return np.packbits(mat, axis=1, bitorder=bitorder).reshape(-1).tobytes()

def scan_stream(stream, label):
    m = secret_re.search(stream)
    if m:
        print(label, "->", m.group().decode())
        return True
    idx = stream.find(base64_prefix)
    if idx != -1:
        j = idx
        while j > 0 and stream[j - 1] in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=":
            j -= 1
        k = idx
        while k < len(stream) and stream[k] in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=":
            k += 1
        token = stream[j:k]
        if len(token) >= 16:
            try:
                dec = base64.b64decode(token, validate=True)
                m = secret_re.search(dec)
                if m:
                    print(label, "->", token.decode(), "=>", m.group().decode())
                    return True
            except Exception:
                pass
    return False

for path in files:
    im = Image.open(path).convert("RGB")
    arr = np.frombuffer(im.tobytes(), dtype=np.uint8).reshape(im.size[1], im.size[0], 3)
    print("==", path.name)
    work0 = arr.reshape(-1, 3)
    for invert in (False, True):
        work1 = 255 - work0 if invert else work0
        for prime in (False, True):
            work = work1
            if prime:
                n = work.shape[0]
                sieve = np.ones(n + 1, dtype=bool)
                sieve[:2] = False
                for p in range(2, int(n ** 0.5) + 1):
                    if sieve[p]:
                        sieve[p * p : n + 1 : p] = False
                work = work[sieve[1:]]
            for shift in range(8):
                shifted = work
                for ordname, seq in orders:
                    for coord in dirs:
                        seq_arr = shifted if coord == "xy" else shifted[::-1]
                        channels = [0 if c == "R" else 1 if c == "G" else 2 for c in seq]
                        flat = np.concatenate([seq_arr[:, c] for c in channels])
                        for bit in range(8):
                            bits = ((flat >> bit) & 1).astype(np.uint8)
                            if shift:
                                bits = np.concatenate([np.zeros(shift, dtype=np.uint8), bits])
                            for bitorder in ("big", "little"):
                                stream = pack_bits(bits, bitorder)
                                label = f"{path.name} inv={invert} prime={prime} shift={shift} {ordname} {coord} bit={bit} {bitorder}"
                                if scan_stream(stream, label):
                                    raise SystemExit(0)
    print("no match")
