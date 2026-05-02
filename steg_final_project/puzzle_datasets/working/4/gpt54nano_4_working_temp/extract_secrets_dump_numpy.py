import os
import json
import re
import itertools
from typing import Dict, List, Optional, Tuple


SECRET_RE = re.compile(rb"secret\{[0-9a-fA-F]{8}\}")


def find_secret_in_bytes(data: bytes) -> Optional[str]:
    m = SECRET_RE.search(data)
    if not m:
        return None
    s = m.group(0).decode("ascii", errors="ignore")
    # Normalize to lowercase hex to be consistent with the dataset style.
    hex_part = s[s.index("{") + 1 : -1].lower()
    return f"secret{{{hex_part}}}"


def stego_index_plans(has_alpha: bool) -> List[Tuple[List[int], List[int], List[int], List[int], str]]:
    # Mirrors the index sets used in stegoveritas/modules/image/analysis/brute_lsb.py
    plans: List[Tuple[List[int], List[int], List[int], List[int], str]] = []

    # Try across same index in RGB: (i,i,i)
    for i in range(8):
        plans.append(([i], [i], [i], [], f"rgb_same_{i}"))

    # Try single channel cumulative: [0..x]
    for x in range(8):
        idx = list(range(0, x + 1))
        plans.append((idx, [], [], [], f"r_cum_{x}"))
        plans.append(([], idx, [], [], f"g_cum_{x}"))
        plans.append(([], [], idx, [], f"b_cum_{x}"))
        if has_alpha:
            plans.append(([], [], [], idx, f"a_cum_{x}"))

    # Try across board cumulative in RGB: (0..x,0..x,0..x)
    for x in range(8):
        idx = list(range(0, x + 1))
        plans.append((idx, idx, idx, [], f"rgb_cum_{x}"))

    # Extra: try extracting only a single bit index from a single channel.
    # This is common in LSB challenges but is not part of stegoveritas' cumulative brute.
    for i in range(8):
        plans.append(([i], [], [], [], f"r_single_{i}"))
        plans.append(([], [i], [], [], f"g_single_{i}"))
        plans.append(([], [], [i], [], f"b_single_{i}"))
        if has_alpha:
            plans.append(([], [], [], [i], f"a_single_{i}"))

    return plans


def dump_bytes_like_dumpLSBRGBA_numpy(
    arr,  # numpy uint8 image array, shape (H,W,C)
    channel_order: List[str],  # e.g. ['R','G','B'] or ['R','B','G'] or with 'A' appended
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

    # Map channel letters to indices in arr last axis.
    # PIL gives bands order in getbands(); for these images it's always ('R','G','B') or ('R','G','B','A').
    # But to be safe, detect from arr shape and rely on caller to provide correct channel_order subset.
    # We'll infer indices by assuming standard ordering when converting to numpy.
    # We only ever call this after splitting arr into named channels.
    channels: Dict[str, np.ndarray] = arr

    if max_bytes <= 0:
        return b""

    r_set = set(red_index)
    g_set = set(green_index)
    b_set = set(blue_index)
    a_set = set(alpha_index)

    index_union = sorted(r_set | g_set | b_set | a_set)
    if not index_union:
        return b""

    if index_order == "desc":
        index_union = index_union[::-1]

    # Build "items" per pixel in the same order as dumpLSBRGBA():
    # for each index in index_union:
    #   for each channel in channel_order:
    #     if index belongs to that channel_index_set, append that bit.
    item_specs: List[Tuple[str, int]] = []
    channel_index_sets: Dict[str, set] = {
        "R": r_set,
        "G": g_set,
        "B": b_set,
        "A": a_set,
    }
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

    # Extract bits only for the first pixels_needed pixels.
    # Each item_spec yields a (pixels_needed,) uint8 array.
    bits_items: List[np.ndarray] = []
    for ch, idx in item_specs:
        cflat = channels[ch]
        sub = cflat[:pixels_needed]
        bits = ((sub >> idx) & 1).astype(np.uint8)
        bits_items.append(bits)

    # bits_items: k x pixels_needed -> pixels_needed x k in item order
    bits2d = np.stack(bits_items, axis=0).T  # shape (pixels_needed, k)
    bitstream = bits2d.reshape(-1)[:bits_needed].copy()

    if invert_bits:
        bitstream ^= 1

    bitorder = "big" if bit_assembly == "msb" else "little"
    chunk = bitstream[offset_bits : offset_bits + required_bits]
    packed = np.packbits(chunk, bitorder=bitorder)
    return packed[:max_bytes].tobytes()


def attempt_image(path: str, max_bytes: int = 65536) -> Optional[Tuple[str, str]]:
    from PIL import Image

    img = Image.open(path)
    mode = img.mode
    has_alpha = "A" in mode

    # Convert to a numpy array but keep channels; PIL will already have the right mode.
    import numpy as np

    if has_alpha:
        img = img.convert("RGBA")
        arr_np = np.array(img, dtype=np.uint8)  # H,W,4
        ch2d = {
            "R": arr_np[:, :, 0],
            "G": arr_np[:, :, 1],
            "B": arr_np[:, :, 2],
            "A": arr_np[:, :, 3],
        }
    else:
        img = img.convert("RGB")
        arr_np = np.array(img, dtype=np.uint8)  # H,W,3
        ch2d = {
            "R": arr_np[:, :, 0],
            "G": arr_np[:, :, 1],
            "B": arr_np[:, :, 2],
        }

    # Pixel traversal variants (flattening order changes bitstream order).
    traversals = [
        ("row", lambda x: x.reshape(-1)),
        ("flipud", lambda x: x[::-1, :].reshape(-1)),
        ("fliplr", lambda x: x[:, ::-1].reshape(-1)),
        ("transpose", lambda x: x.T.reshape(-1)),
        ("flipud_transpose", lambda x: x[::-1, :].T.reshape(-1)),
    ]

    plans = stego_index_plans(has_alpha=has_alpha)

    # Channel order search: start with standard, then a few permutations.
    rgb_perms = list(itertools.permutations(["R", "G", "B"], 3))
    channel_orders = []
    if has_alpha:
        # Keep alpha last; encoding likely appends alpha bits last in channel-order.
        for perm in rgb_perms:
            channel_orders.append(list(perm) + ["A"])
    else:
        channel_orders = [list(perm) for perm in rgb_perms]

    # Search strategy: exact-ish first.
    search_specs = [
        ("asc", "msb", False),  # match stegoveritas default
        ("asc", "msb", True),   # invert bits
        ("asc", "lsb", False),  # different bit assembly
        ("desc", "msb", False), # reversed bit index order
    ]

    offsets_to_try = list(range(8))

    # Try standard channel order first before permutations.
    preferred = []
    if has_alpha:
        preferred = [["R", "G", "B", "A"], ["B", "G", "R", "A"]]
    else:
        preferred = [["R", "G", "B"], ["B", "G", "R"]]

    ordered_channel_orders = []
    for p in preferred:
        if p in channel_orders:
            ordered_channel_orders.append(p)
    for co in channel_orders:
        if co not in ordered_channel_orders:
            ordered_channel_orders.append(co)

    for trav_name, flat_fn in traversals:
        channels = {ch: flat_fn(ch2d[ch]) for ch in ch2d}
        for red_i, green_i, blue_i, alpha_i, plan_label in plans:
            for index_order, assembly, invert in search_specs:
                for ch_order in ordered_channel_orders:
                    for offset_bits in offsets_to_try:
                        dumped = dump_bytes_like_dumpLSBRGBA_numpy(
                            arr=channels,
                            channel_order=ch_order,
                            red_index=red_i,
                            green_index=green_i,
                            blue_index=blue_i,
                            alpha_index=alpha_i,
                            max_bytes=max_bytes,
                            offset_bits=offset_bits,
                            bit_assembly=assembly,
                            invert_bits=invert,
                            index_order=index_order,
                        )
                        s = find_secret_in_bytes(dumped)
                        if s:
                            label = (
                                f"{plan_label};trav={trav_name};ch={''.join(ch_order)};"
                                f"idx={index_order};asm={assembly};inv={int(invert)};off={offset_bits}"
                            )
                            return s, label
    return None


def main() -> None:
    secrets_path = "working_temp/secrets.json"
    with open(secrets_path, "r") as f:
        items = json.load(f)

    missing = [it for it in items if not it.get("secret")]
    if not missing:
        print("No missing secrets.")
        return

    updated = 0
    for it in missing:
        rel = it["path"]
        if not os.path.exists(rel):
            print(f"{rel}: missing file")
            continue
        res = attempt_image(rel, max_bytes=65536)
        if res:
            secret, label = res
            it["secret"] = secret
            it["method"] = f"dumpLSB-numpy({label})"
            updated += 1
            print(f"{rel}: {secret}")
        else:
            print(f"{rel}: NOT_FOUND")

        # Persist incrementally so we never lose progress.
        with open(secrets_path, "w") as wf:
            json.dump(items, wf, indent=2)

    print(f"Updated {updated}/{len(missing)}")


if __name__ == "__main__":
    main()
