import os
import json
import itertools
from typing import Optional, List, Dict
import subprocess


# Same secret matcher as the first script.
import re

PATTERN = b"secret{"
SECRET_RE = re.compile(r"^secret\{[0-9a-fA-F]{8}\}$")


def find_secret_in_bytes(data: bytes) -> Optional[str]:
    start = 0
    while True:
        i = data.find(PATTERN, start)
        if i < 0:
            return None
        j = i + len(PATTERN)
        k = j + 8
        if k < len(data) and data[k] == ord("}"):
            candidate = data[i : k + 1].decode("ascii", errors="ignore")
            if SECRET_RE.match(candidate):
                return candidate
        start = i + 1


def concat_chunk(segments: List, offset: int, nbits: int):
    # segments: list of 1D numpy arrays of bits in {0,1}
    out = segments[0].dtype
    import numpy as np

    res = np.empty(nbits, dtype=np.uint8)
    need = nbits
    pos = 0
    skip = offset
    for seg in segments:
        if skip >= seg.size:
            skip -= seg.size
            continue
        start = skip
        take = min(seg.size - start, need)
        if take <= 0:
            continue
        res[pos : pos + take] = seg[start : start + take]
        pos += take
        need -= take
        skip = 0
        if need == 0:
            break
    return res


def try_lsb_numpy_advanced(path: str, max_bytes: int = 8192) -> Optional[str]:
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return None

    img = Image.open(path)
    arr = np.array(img)

    # Normalize to either (H,W) or (H,W,C)
    if arr.ndim == 2:
        h, w = arr.shape
        channels = {"c0": 0}
        arr_c = arr[:, :, None]
    elif arr.ndim == 3:
        h, w, c = arr.shape
        if c >= 4:
            arr_c = arr[:, :, :4]
            channels = {"r": 0, "g": 1, "b": 2, "a": 3}
        else:
            arr_c = arr[:, :, :3]
            channels = {"r": 0, "g": 1, "b": 2}
    else:
        return None

    rgb_perms = ["".join(p) for p in itertools.permutations(["r", "g", "b"], 3)]

    orders: List[str] = []
    if set(channels.keys()) == {"c0"}:
        orders = ["c0"]
    else:
        orders = rgb_perms + ["r", "g", "b"]
        if "a" in channels:
            # Keep alpha-including orders limited to avoid combinatorics.
            orders += ["a", "rgba", "rbga", "grba"]

    traversals = [
        ("normal", arr_c),
        ("flipud", arr_c[::-1, :, :]),
        ("fliplr", arr_c[:, ::-1, :]),
        ("flipudfliplr", arr_c[::-1, ::-1, :]),
    ]

    pack_modes = ["big", "little"]
    bit_planes = list(range(8))
    offsets = list(range(8))

    # Staged search: first interleave without inversion, then interleave with inversion,
    # then concat without inversion, then concat with inversion.
    search_stages = [
        ("interleave", False),
        ("interleave", True),
        ("concat", False),
        ("concat", True),
    ]

    for mode, invert in search_stages:
        for _, a in traversals:
            for plane in bit_planes:
                bits_ch = (a >> plane) & 1  # (H,W,C)

                for order in orders:
                    idxs: List[int] = []
                    ok = True
                    for ch in order:
                        if ch not in channels:
                            ok = False
                            break
                        idxs.append(channels[ch])
                    if not ok or not idxs:
                        continue

                    if mode == "interleave":
                        bits_seq = bits_ch[:, :, idxs].reshape(-1).astype(np.uint8)
                        total_bits = int(bits_seq.size)
                    else:
                        segments = [bits_ch[:, :, i].reshape(-1).astype(np.uint8) for i in idxs]
                        total_bits = int(segments[0].size * len(idxs))

                    for pack_mode in pack_modes:
                        for offset in offsets:
                            if offset >= total_bits:
                                continue
                            remaining_bits = total_bits - offset
                            byte_count = min(remaining_bits // 8, max_bytes)
                            if byte_count <= 0:
                                continue

                            nbits = byte_count * 8
                            if mode == "interleave":
                                chunk = bits_seq[offset : offset + nbits].copy()
                            else:
                                chunk = concat_chunk(segments, offset, nbits)

                            if invert:
                                chunk ^= 1

                            packed = np.packbits(chunk, bitorder=pack_mode)
                            data = packed[:byte_count].tobytes()
                            found = find_secret_in_bytes(data)
                            if found:
                                return found

    return None


def main() -> None:
    with open("working_temp/secrets.json", "r") as f:
        items = json.load(f)

    missing = [it for it in items if not it.get("secret")]
    updates = 0

    for it in missing:
        path = it["path"]
        if not os.path.exists(path):
            continue
        found = try_lsb_numpy_advanced(path)
        if found:
            it["secret"] = found
            it["method"] = "lsb-advanced"
            updates += 1
            print(f"{path}: {found}")
        else:
            print(f"{path}: NOT_FOUND")

    out_path = "working_temp/secrets_advanced.json"
    with open(out_path, "w") as f:
        json.dump(items, f, indent=2)

    print(f"Updated: {updates}/{len(missing)}")


if __name__ == "__main__":
    main()

