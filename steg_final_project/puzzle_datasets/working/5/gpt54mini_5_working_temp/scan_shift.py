from pathlib import Path
from PIL import Image
import base64
import numpy as np
import re

secret_re = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")
base64_prefix = b"c2VjcmV0"

files = [Path("puzzle_0006.png"), Path("puzzle_0012.png")]
orders = [
    ("rgb", ("R", "G", "B")),
    ("bgr", ("B", "G", "R")),
    ("r", ("R",)),
    ("g", ("G",)),
    ("b", ("B",)),
]
dirs = ["xy", "yx"]

def pack(bits):
    n = (len(bits) // 8) * 8
    if n < 8:
        return b""
    return np.packbits(bits[:n].reshape(-1, 8), axis=1, bitorder="big").reshape(-1).tobytes()

for path in files:
    arr = np.frombuffer(Image.open(path).convert("RGB").tobytes(), dtype=np.uint8).reshape(-1, 3)
    print("==", path.name)
    for invert in (False, True):
        work = 255 - arr if invert else arr
        for shift in range(8):
            for ordname, seq in orders:
                for coord in dirs:
                    seq_arr = work if coord == "xy" else work[::-1]
                    channels = [0 if c == "R" else 1 if c == "G" else 2 for c in seq]
                    flat = np.concatenate([seq_arr[:, c] for c in channels])
                    for bit in range(8):
                        bits = ((flat >> bit) & 1).astype(np.uint8)
                        if shift:
                            bits = np.concatenate([np.zeros(shift, dtype=np.uint8), bits])
                        for bo in ("big", "little"):
                            stream = np.packbits(bits[: (len(bits) // 8) * 8].reshape(-1, 8), axis=1, bitorder=bo).reshape(-1).tobytes()
                            m = secret_re.search(stream)
                            if m:
                                print("hit", path.name, invert, shift, ordname, coord, bit, bo, m.group().decode())
                                raise SystemExit(0)
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
                                    except Exception:
                                        continue
                                    m = secret_re.search(dec)
                                    if m:
                                        print("hit", path.name, invert, shift, ordname, coord, bit, bo, token.decode(), "=>", m.group().decode())
                                        raise SystemExit(0)
    print("no hit")
