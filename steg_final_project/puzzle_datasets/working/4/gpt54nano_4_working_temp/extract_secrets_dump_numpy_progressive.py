import os
import json
import re
from typing import Dict, List, Optional, Tuple

import itertools

SECRET_RE_BYTES = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def find_secret_in_bytes(data: bytes) -> Optional[str]:
    m = SECRET_RE_BYTES.search(data)
    if not m:
        return None
    s = m.group(0).decode("ascii", errors="ignore")
    hex_part = s[s.index("{") + 1 : -1].lower()
    return f"secret{{{hex_part}}}"


def stego_index_plans(has_alpha: bool) -> List[Tuple[List[int], List[int], List[int], List[int], str]]:
    plans: List[Tuple[List[int], List[int], List[int], List[int], str]] = []

    # stegoveritas-like core patterns
    for i in range(8):
        plans.append(([i], [i], [i], [], f"rgb_same_{i}"))

    for x in range(8):
        idx = list(range(0, x + 1))
        plans.append((idx, [], [], [], f"r_cum_{x}"))
        plans.append(([], idx, [], [], f"g_cum_{x}"))
        plans.append(([], [], idx, [], f"b_cum_{x}"))
        if has_alpha:
            plans.append(([], [], [], idx, f"a_cum_{x}"))

    for x in range(8):
        idx = list(range(0, x + 1))
        plans.append((idx, idx, idx, [], f"rgb_cum_{x}"))

    # extra: single index on single channel
    for i in range(8):
        plans.append(([i], [], [], [], f"r_single_{i}"))
        plans.append(([], [i], [], [], f"g_single_{i}"))
        plans.append(([], [], [i], [], f"b_single_{i}"))
        if has_alpha:
            plans.append(([], [], [], [i], f"a_single_{i}"))

    return plans


def dump_bytes_like_dumpLSBRGBA_numpy(
    channels: Dict[str, "np.ndarray"],  # flattened uint8 per channel
    channel_order: List[str],
    red_index: List[int],
    green_index: List[int],
    blue_index: List[int],
    alpha_index: List[int],
    max_bytes: int,
    offset_bits: int,
    bit_assembly: str,  # 'msb' or 'lsb'
    invert_bits: bool,
    index_order: str,  # 'asc' or 'desc'
) -> bytes:
    import numpy as np

    r_set = set(red_index)
    g_set = set(green_index)
    b_set = set(blue_index)
    a_set = set(alpha_index)

    index_union = sorted(r_set | g_set | b_set | a_set)
    if not index_union:
        return b""
    if index_order == "desc":
        index_union = index_union[::-1]

    channel_index_sets = {"R": r_set, "G": g_set, "B": b_set, "A": a_set}
    item_specs: List[Tuple[str, int]] = []
    for idx in index_union:
        for ch in channel_order:
            if idx in channel_index_sets[ch]:
                item_specs.append((ch, idx))

    k = len(item_specs)
    if k == 0:
        return b""

    required_bits = max_bytes * 8
    bits_needed = offset_bits + required_bits
    pixels_needed = (bits_needed + k - 1) // k

    bits_items = []
    for ch, idx in item_specs:
        sub = channels[ch][:pixels_needed]
        bits = ((sub >> idx) & 1).astype(np.uint8)
        bits_items.append(bits)

    bits2d = np.stack(bits_items, axis=0).T  # pixels_needed x k
    bitstream = bits2d.reshape(-1)[:bits_needed].copy()
    if invert_bits:
        bitstream ^= 1

    chunk = bitstream[offset_bits : offset_bits + required_bits]
    bitorder = "big" if bit_assembly == "msb" else "little"
    packed = np.packbits(chunk, bitorder=bitorder)
    return packed[:max_bytes].tobytes()


def quick_direct_scan(path: str) -> Optional[str]:
    with open(path, "rb") as f:
        data = f.read()
    return find_secret_in_bytes(data)


def attempt_image_progressive(path: str, max_bytes: int = 32768) -> Optional[Tuple[str, str]]:
    from PIL import Image
    import numpy as np

    direct = quick_direct_scan(path)
    if direct:
        return direct, "direct-bytes"

    img = Image.open(path)
    has_alpha = "A" in img.mode

    if has_alpha:
        img = img.convert("RGBA")
        arr = np.array(img, dtype=np.uint8)
        ch2d = {"R": arr[:, :, 0], "G": arr[:, :, 1], "B": arr[:, :, 2], "A": arr[:, :, 3]}
    else:
        img = img.convert("RGB")
        arr = np.array(img, dtype=np.uint8)
        ch2d = {"R": arr[:, :, 0], "G": arr[:, :, 1], "B": arr[:, :, 2]}

    # Only a small traversal set first for speed.
    traversals = [("row", lambda x: x.reshape(-1))]
    if has_alpha:
        traversals += [
            ("flipud", lambda x: x[::-1, :].reshape(-1)),
            ("fliplr", lambda x: x[:, ::-1].reshape(-1)),
        ]

    plans = stego_index_plans(has_alpha=has_alpha)

    # Channel order: standard and R/B swapped.
    if has_alpha:
        ordered_channel_orders = [["R", "G", "B", "A"], ["B", "G", "R", "A"]]
    else:
        ordered_channel_orders = [["R", "G", "B"], ["B", "G", "R"]]

    # Progressive search levels.
    levels = [
        # (index_order, bit_assembly, invert_bits, offsets)
        ("asc", "msb", False, [0, 1, 2, 3, 4, 5, 6, 7]),
        ("asc", "msb", True, [0, 1, 2, 3, 4, 5, 6, 7]),
        ("desc", "msb", False, [0, 1, 2, 3, 4, 5, 6, 7]),
        ("asc", "lsb", False, [0, 1, 2, 3, 4, 5, 6, 7]),
    ]

    for trav_name, flat_fn in traversals:
        channels = {ch: flat_fn(ch2d[ch]) for ch in ch2d}

        for red_i, green_i, blue_i, alpha_i, plan_label in plans:
            for index_order, assembly, invert, offsets in levels:
                for ch_order in ordered_channel_orders:
                    for off in offsets:
                        dumped = dump_bytes_like_dumpLSBRGBA_numpy(
                            channels=channels,
                            channel_order=ch_order,
                            red_index=red_i,
                            green_index=green_i,
                            blue_index=blue_i,
                            alpha_index=alpha_i,
                            max_bytes=max_bytes,
                            offset_bits=off,
                            bit_assembly=assembly,
                            invert_bits=invert,
                            index_order=index_order,
                        )
                        s = find_secret_in_bytes(dumped)
                        if s:
                            label = f"{plan_label};trav={trav_name};ch={''.join(ch_order)};idx={index_order};asm={assembly};inv={int(invert)};off={off}"
                            return s, label

    # If not found, try a larger byte window once.
    if max_bytes < 65536:
        return attempt_image_progressive(path, max_bytes=65536)

    return None


def main() -> None:
    secrets_path = "working_temp/secrets.json"
    with open(secrets_path, "r") as f:
        items = json.load(f)

    missing = [it for it in items if not it.get("secret")]
    updated = 0

    for it in missing:
        rel = it["path"]
        print(f"Trying {rel} ...")
        res = attempt_image_progressive(rel, max_bytes=32768)
        if res:
            secret, label = res
            it["secret"] = secret
            it["method"] = f"dumpLSB-progressive({label})"
            updated += 1
            print(f"FOUND {rel} -> {secret}")
        else:
            print(f"NOT_FOUND {rel}")

        with open(secrets_path, "w") as wf:
            json.dump(items, wf, indent=2)

    print(f"Updated {updated}/{len(missing)}")


if __name__ == "__main__":
    main()

