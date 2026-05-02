import os
import re
import json
from typing import List, Optional, Tuple

from PIL import Image


SECRET_RE_BYTES = re.compile(br"secret\{[0-9a-fA-F]{8}\}")


def find_secret_in_bytes(data: bytes) -> Optional[str]:
    m = SECRET_RE_BYTES.search(data)
    if not m:
        return None
    return m.group(0).decode("ascii", errors="ignore")


def dump_lsbrgba_first_bytes(
    img: Image.Image,
    red_index: List[int],
    green_index: List[int],
    blue_index: List[int],
    alpha_index: List[int],
    max_bytes: int,
) -> bytes:
    # Replicates stegoveritas.modules.image.SVImage.dumpLSBRGBA() ordering:
    # - iterate bytes in RGBA band order (red,green,blue,alpha) from tobytes()
    # - for each pixel: for index in sorted(union(index sets)):
    #   append bit from each channel whose index list contains that index
    # - then group bits into bytes MSB-first.
    if img.mode == "P":
        # Shouldn't happen for this dataset (file says RGB/RGBA), but keep safe.
        img = img.convert("RGBA")

    bands = img.getbands()
    fBytes = img.tobytes()

    # Channel byte offsets in stegoveritas assumed RGB(A) order.
    # For our images: ('R','G','B') or ('R','G','B','A').
    has_alpha = "A" in bands

    width, height = img.size
    pixel_stride = len(bands)  # 3 or 4
    pixel_count = width * height

    red_set = set(red_index)
    green_set = set(green_index)
    blue_set = set(blue_index)
    alpha_set = set(alpha_index)

    indexes = sorted(red_set | green_set | blue_set | alpha_set)
    if not indexes:
        return b""

    needed_bits = max_bytes * 8
    out = bytearray()

    current = 0
    bit_filled = 0
    bits_emitted = 0

    # Walk pixels in the same order stegoveritas uses: sequential over tobytes()
    for p in range(pixel_count):
        base = p * pixel_stride
        for index in indexes:
            # Red
            if index in red_set:
                b = (fBytes[base + 0] >> index) & 1
                current = (current << 1) | b
                bit_filled += 1
                bits_emitted += 1
            # Green
            if index in green_set:
                b = (fBytes[base + 1] >> index) & 1
                current = (current << 1) | b
                bit_filled += 1
                bits_emitted += 1
            # Blue
            if index in blue_set:
                b = (fBytes[base + 2] >> index) & 1
                current = (current << 1) | b
                bit_filled += 1
                bits_emitted += 1
            # Alpha (only if image has alpha and caller provided indices)
            if has_alpha and index in alpha_set:
                b = (fBytes[base + 3] >> index) & 1
                current = (current << 1) | b
                bit_filled += 1
                bits_emitted += 1

            while bit_filled >= 8:
                # bit_filled should never exceed 8 by more than 1 in this loop,
                # but keep it correct.
                shift = bit_filled - 8
                byte_val = (current >> shift) & 0xFF
                out.append(byte_val)
                bit_filled -= 8
                if shift:
                    current = current & ((1 << shift) - 1)
                else:
                    current = 0
                if len(out) >= max_bytes:
                    return bytes(out[:max_bytes])

            if bits_emitted >= needed_bits:
                break
        if bits_emitted >= needed_bits:
            break

    return bytes(out)


def gen_stegoveritas_like_index_sets(has_alpha: bool) -> List[Tuple[List[int], List[int], List[int], List[int], str]]:
    # Mirrors stegoveritas brute_lsb.py:
    # - same index across RGB: red=[i], green=[i], blue=[i]
    # - cumulative on a single channel: red=[0..x] (x=0..7), green/blue empty (similar for G/B/A)
    # - across-the-board cumulative: red=[0..x], green=[0..x], blue=[0..x]
    sets: List[Tuple[List[int], List[int], List[int], List[int], str]] = []

    for i in range(8):
        sets.append(([i], [i], [i], [], f"rgb_same_{i}"))

    for x in range(8):
        idx = list(range(0, x + 1))
        sets.append((idx, [], [], [], f"r_cum_{x}"))
        sets.append(([], idx, [], [], f"g_cum_{x}"))
        sets.append(([], [], idx, [], f"b_cum_{x}"))
        if has_alpha:
            sets.append(([], [], [], idx, f"a_cum_{x}"))

    for x in range(8):
        idx = list(range(0, x + 1))
        sets.append((idx, idx, idx, [], f"rgb_cum_{x}"))

    return sets


def main() -> None:
    # Start from the first extraction attempt's missing list.
    with open("working_temp/secrets.json", "r") as f:
        items = json.load(f)

    targets = [it for it in items if not it.get("secret")]
    updates = 0

    results_path = "working_temp/secrets_dumpLSB.json"
    # Copy original for non-missing as well.
    out_items = items

    for it in targets:
        rel = it["path"]
        if not os.path.exists(rel):
            continue

        img = Image.open(rel)
        has_alpha = img.mode in ("RGBA", "LA") or ("A" in img.getbands())
        index_sets = gen_stegoveritas_like_index_sets(has_alpha=has_alpha)

        found = None
        found_method = None

        # Dump only enough bytes to cover the secret and a bit of slack.
        # Since this dataset uses a fixed 16-byte-length pattern, 64KiB is plenty.
        for red_i, green_i, blue_i, alpha_i, label in index_sets:
            dumped = dump_lsbrgba_first_bytes(
                img=img,
                red_index=red_i,
                green_index=green_i,
                blue_index=blue_i,
                alpha_index=alpha_i,
                max_bytes=65536,
            )
            s = find_secret_in_bytes(dumped)
            if s:
                found = s
                found_method = label
                break

        if found:
            it["secret"] = found
            it["method"] = f"dumpLSB({found_method})"
            updates += 1
            print(f"{rel}: {found} ({found_method})")
        else:
            print(f"{rel}: NOT_FOUND")

    with open(results_path, "w") as f:
        json.dump(out_items, f, indent=2)

    print(f"Recovered {updates}/{len(targets)} secrets with dumpLSB brute-force.")


if __name__ == "__main__":
    main()

