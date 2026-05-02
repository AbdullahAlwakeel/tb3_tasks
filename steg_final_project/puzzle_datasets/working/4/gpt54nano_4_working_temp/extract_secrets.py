import os
import re
import glob
import json
import subprocess
from dataclasses import dataclass
from typing import Optional, List, Dict


PATTERN = b"secret{"
SECRET_RE = re.compile(r"^secret\{[0-9a-fA-F]{8}\}$")


def find_secret_in_bytes(data: bytes) -> Optional[str]:
    # Fast path: find 'secret{' then validate 8 hex chars + trailing '}'
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


def scan_direct_bytes(path: str) -> Optional[str]:
    with open(path, "rb") as f:
        data = f.read()
    return find_secret_in_bytes(data)


def scan_exiftool(path: str) -> Optional[str]:
    try:
        # -s short output, -n numeric values; keeps output parseable.
        # exiftool returns non-zero sometimes; we still inspect stdout/stderr.
        p = subprocess.run(
            ["exiftool", "-s", "-n", path],
            capture_output=True,
            text=True,
            check=False,
        )
        out = (p.stdout or "") + "\n" + (p.stderr or "")
        m = re.search(r"secret\{[0-9a-fA-F]{8}\}", out)
        if m:
            return m.group(0)
    except FileNotFoundError:
        return None
    return None


def try_lsb_numpy(path: str, max_bytes: int = 8192) -> Optional[str]:
    # Uses numpy for speed. Falls back to None if numpy/PIL unavailable.
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return None

    img = Image.open(path)
    arr = np.array(img)

    # Normalize to either (H,W) (single channel) or (H,W,C) with channels last.
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

    # Candidate channel orders (interleaving across a pixel in order).
    channel_orders: List[str] = []
    if set(channels.keys()) == {"r", "g", "b", "a"}:
        channel_orders = ["rgba", "rgb", "r", "g", "b", "a", "rg", "gb", "rb", "ag", "ab"]
    else:
        channel_orders = ["rgb", "r", "g", "b", "rg", "gb", "rb"]

    traversals = [("normal", arr_c), ("flipud", arr_c[::-1, :, :])]

    # Packing: numpy.packbits uses 'big' where the first bit is MSB of the byte.
    pack_modes = ["big", "little"]

    # Bit-plane to try.
    bit_planes = list(range(8))

    for _, a in traversals:
        for plane in bit_planes:
            # bits[...,ch] in {0,1}
            bits_ch = (a >> plane) & 1

            for order in channel_orders:
                idxs: List[int] = []
                ok = True
                for ch in order:
                    if ch not in channels:
                        ok = False
                        break
                    idxs.append(channels[ch])
                if not ok or not idxs:
                    continue

                # Interleave bits across channels for each pixel.
                # We only need the first `max_bytes` decoded bytes for each offset,
                # so we slice the bitstream late.
                bits_seq = bits_ch[:, :, idxs].reshape(-1).astype(np.uint8)
                total_bits = int(bits_seq.size)

                for pack_mode in pack_modes:
                    # Offsets in bits (0..7)
                    for offset in range(8):
                        if offset >= total_bits:
                            continue
                        remaining_bits = total_bits - offset
                        byte_count = min(remaining_bits // 8, max_bytes)
                        if byte_count <= 0:
                            continue

                        nbits = byte_count * 8
                        chunk = bits_seq[offset : offset + nbits]
                        packed = np.packbits(chunk, bitorder=pack_mode)
                        data = packed[:byte_count].tobytes()

                        found = find_secret_in_bytes(data)
                        if found:
                            return found

    return None


@dataclass
class Result:
    path: str
    secret: Optional[str]
    method: str


def scan_binwalk_extract(path: str, workdir: str) -> Optional[str]:
    # Extract embedded blobs, then scan extracted files for a secret string.
    try:
        os.makedirs(workdir, exist_ok=True)
        # --dd extracts data blocks; -e extracts identified components.
        subprocess.run(
            ["binwalk", "-e", "--directory", workdir, path],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None

    # Scan all files under workdir.
    for p in glob.glob(os.path.join(workdir, "**/*"), recursive=True):
        if os.path.isdir(p):
            continue
        try:
            with open(p, "rb") as f:
                data = f.read()
            found = find_secret_in_bytes(data)
            if found:
                return found
        except Exception:
            continue
    return None


def main() -> None:
    os.makedirs("working_temp", exist_ok=True)
    images = sorted(glob.glob("puzzle_*.png"))
    results: List[Result] = []

    for img_path in images:
        secret = None
        method = None

        secret = scan_direct_bytes(img_path)
        if secret:
            method = "direct-bytes"
        else:
            secret = scan_exiftool(img_path)
            if secret:
                method = "exiftool"
            else:
                secret = try_lsb_numpy(img_path)
                if secret:
                    method = "lsb-numpy"
                else:
                    # Last resort: binwalk extraction + scan.
                    bw_dir = os.path.join("working_temp", f"binwalk_{os.path.basename(img_path)}")
                    secret = scan_binwalk_extract(img_path, bw_dir)
                    method = "binwalk" if secret else "not-found"

        results.append(Result(img_path, secret, method or "unknown"))
        print(f"{img_path}: {secret or 'NOT_FOUND'} ({method})")

    out_path = os.path.join("working_temp", "secrets.json")
    with open(out_path, "w") as f:
        json.dump([r.__dict__ for r in results], f, indent=2)

    # Also write a flat answer file.
    flat = "\n".join(
        f"{os.path.basename(r.path)} {r.secret or ''}".strip() for r in results
    )
    with open(os.path.join("working_temp", "secrets.txt"), "w") as f:
        f.write(flat + "\n")


if __name__ == "__main__":
    main()
