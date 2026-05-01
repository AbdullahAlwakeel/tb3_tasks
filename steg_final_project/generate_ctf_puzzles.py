"""
Generate beginner-friendly CTF steganography puzzles.

Each output image contains exactly one secret. Depth-1 puzzles hide the secret
with one technique. Depth-2 puzzles wrap an inner stego artifact inside an outer
technique, so solving requires peeling the outer layer and then solving the
inner layer. The same secret is not embedded twice in the final image.
"""

from __future__ import annotations

import argparse
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw

from stego_transforms import (
    AlphaEmbed,
    AppendEmbed,
    LSBEmbed,
    LowContrastEmbed,
    MetadataEmbed,
    _load_readable_font,
    image_to_bytes,
    save,
    text_to_bytes,
)


NUM_PUZZLES = {1: 5, 2: 5}
SRC_DIR = Path("src_images")
OUT_DIR = Path("ctf_puzzles")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class PuzzleTemplate:
    name: str
    depth: int
    build: Callable[[Image.Image, str, random.Random], tuple[Image.Image, str]]


def secret_for(rng: random.Random) -> str:
    return f"secret{{{rng.getrandbits(32):08x}}}"


def source_images(src_dir: Path) -> list[Path]:
    images = sorted(
        path for path in src_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not images:
        raise ValueError(f"No source images found in {src_dir}.")
    return images


def inner_canvas(secret: str, rng: random.Random) -> Image.Image:
    """
    Create a small generated image for inner-layer puzzles.

    This keeps nested payload sizes small enough for beginner LSB/alpha puzzles.
    """
    palette = [
        ((245, 245, 245), (40, 40, 40)),
        ((232, 240, 255), (30, 46, 75)),
        ((245, 238, 224), (54, 42, 30)),
    ]
    bg, fg = rng.choice(palette)
    img = Image.new("RGB", (520, 160), bg)
    draw = ImageDraw.Draw(img)

    font = _load_readable_font(24)

    # Non-secret label helps solvers understand the extracted artifact is an
    # intermediate image, but the secret itself is added only by the inner method.
    draw.text((18, 18), "inner artifact", fill=fg, font=font)
    return img


def smooth_text_position(image: Image.Image, box: tuple[int, int] = (520, 120)) -> tuple[int, int]:
    gray = np.array(image.convert("L"), dtype=np.float32)
    box_w = min(box[0], image.width)
    box_h = min(box[1], image.height)
    step = max(20, min(box_w, box_h) // 4)

    best_score = float("inf")
    best = (10, 10)
    for y in range(0, max(1, image.height - box_h + 1), step):
        for x in range(0, max(1, image.width - box_w + 1), step):
            patch = gray[y : y + box_h, x : x + box_w]
            score = float(patch.std())
            if score < best_score:
                best_score = score
                best = (x + 10, y + 10)
    return best


def one_lsb(cover: Image.Image, secret: str, rng: random.Random) -> tuple[Image.Image, str]:
    return LSBEmbed.embed(text_to_bytes(secret), cover), "zsteg b1,rgb,lsb,xy"


def one_alpha(cover: Image.Image, secret: str, rng: random.Random) -> tuple[Image.Image, str]:
    return AlphaEmbed.embed(text_to_bytes(secret), cover), "zsteg b1,a,lsb,xy"


def one_append(cover: Image.Image, secret: str, rng: random.Random) -> tuple[Image.Image, str]:
    return AppendEmbed.embed(text_to_bytes(secret), cover), "strings or inspect PNG trailer data"


def one_metadata(cover: Image.Image, secret: str, rng: random.Random) -> tuple[Image.Image, str]:
    return MetadataEmbed.embed(text_to_bytes(secret), cover), "exiftool ImageDescription -> base64 decode -> skip 4-byte length prefix"


def one_low_contrast(cover: Image.Image, secret: str, rng: random.Random) -> tuple[Image.Image, str]:
    position = smooth_text_position(cover)
    return (
        LowContrastEmbed.embed(
            text_to_bytes(secret),
            cover,
            delta=8,
            position=position,
            font_size=36,
            wrap_width=60,
        ),
        f"visually enhance local contrast or inspect manually near position={position}",
    )


def inner_lsb_image(secret: str, rng: random.Random) -> Image.Image:
    return LSBEmbed.embed(text_to_bytes(secret), inner_canvas(secret, rng))


def inner_alpha_image(secret: str, rng: random.Random) -> Image.Image:
    return AlphaEmbed.embed(text_to_bytes(secret), inner_canvas(secret, rng))


def inner_metadata_image(secret: str, rng: random.Random) -> Image.Image:
    return MetadataEmbed.embed(text_to_bytes(secret), inner_canvas(secret, rng))


def inner_low_contrast_image(secret: str, rng: random.Random) -> Image.Image:
    return LowContrastEmbed.embed(
        text_to_bytes(secret),
        inner_canvas(secret, rng),
        delta=8,
        position=(18, 72),
        font_size=36,
        wrap_width=60,
    )


def two_outer_lsb_inner_metadata(
    cover: Image.Image, secret: str, rng: random.Random
) -> tuple[Image.Image, str]:
    inner = inner_metadata_image(secret, rng)
    outer = LSBEmbed.embed(image_to_bytes(inner), cover)
    return outer, "zsteg b1,rgb,lsb,xy -> skip 4-byte length prefix -> inner PNG -> exiftool metadata"


def two_outer_alpha_inner_metadata(
    cover: Image.Image, secret: str, rng: random.Random
) -> tuple[Image.Image, str]:
    inner = inner_metadata_image(secret, rng)
    outer = AlphaEmbed.embed(image_to_bytes(inner), cover)
    return outer, "zsteg b1,a,lsb,xy -> skip 4-byte length prefix -> inner PNG -> exiftool metadata"


def two_outer_metadata_inner_lsb(
    cover: Image.Image, secret: str, rng: random.Random
) -> tuple[Image.Image, str]:
    inner = inner_lsb_image(secret, rng)
    outer = MetadataEmbed.embed(image_to_bytes(inner), cover)
    return outer, "exiftool metadata -> base64 decode -> skip 4-byte length prefix -> inner PNG -> zsteg b1,rgb,lsb,xy"


def two_outer_append_inner_lsb(
    cover: Image.Image, secret: str, rng: random.Random
) -> tuple[Image.Image, str]:
    inner = inner_lsb_image(secret, rng)
    outer = AppendEmbed.embed(image_to_bytes(inner), cover)
    return outer, "extract PNG trailer payload -> skip 4-byte length prefix -> inner PNG -> zsteg b1,rgb,lsb,xy"


def two_outer_lsb_inner_low_contrast(
    cover: Image.Image, secret: str, rng: random.Random
) -> tuple[Image.Image, str]:
    inner = inner_low_contrast_image(secret, rng)
    outer = LSBEmbed.embed(image_to_bytes(inner), cover)
    return outer, "zsteg b1,rgb,lsb,xy -> skip 4-byte length prefix -> inner PNG -> visually enhance/read text"


def two_outer_alpha_inner_low_contrast(
    cover: Image.Image, secret: str, rng: random.Random
) -> tuple[Image.Image, str]:
    inner = inner_low_contrast_image(secret, rng)
    outer = AlphaEmbed.embed(image_to_bytes(inner), cover)
    return outer, "zsteg b1,a,lsb,xy -> skip 4-byte length prefix -> inner PNG -> visually enhance/read text"


TEMPLATES = [
    PuzzleTemplate("lsb_text", 1, one_lsb),
    PuzzleTemplate("alpha_lsb_text", 1, one_alpha),
    PuzzleTemplate("append_text", 1, one_append),
    PuzzleTemplate("metadata_text", 1, one_metadata),
    PuzzleTemplate("low_contrast_text", 1, one_low_contrast),
    PuzzleTemplate("lsb_wraps_metadata_image", 2, two_outer_lsb_inner_metadata),
    PuzzleTemplate("alpha_wraps_metadata_image", 2, two_outer_alpha_inner_metadata),
    PuzzleTemplate("metadata_wraps_lsb_image", 2, two_outer_metadata_inner_lsb),
    PuzzleTemplate("append_wraps_lsb_image", 2, two_outer_append_inner_lsb),
    PuzzleTemplate("lsb_wraps_low_contrast_image", 2, two_outer_lsb_inner_low_contrast),
    PuzzleTemplate("alpha_wraps_low_contrast_image", 2, two_outer_alpha_inner_low_contrast),
]


def choose_templates(num_puzzles: dict[int, int], rng: random.Random) -> list[PuzzleTemplate]:
    chosen: list[PuzzleTemplate] = []
    for depth, count in sorted(num_puzzles.items()):
        if depth not in (1, 2):
            raise ValueError("This beginner generator supports only depths 1 and 2.")
        available = [template for template in TEMPLATES if template.depth == depth]
        if count > len(available):
            raise ValueError(
                f"Requested {count} depth-{depth} puzzles, but only "
                f"{len(available)} unique templates are available."
            )
        chosen.extend(rng.sample(available, count))
    return chosen


def parse_num_puzzles(value: str) -> dict[int, int]:
    result: dict[int, int] = {}
    for item in value.split(","):
        depth, count = item.split(":", 1)
        result[int(depth.strip())] = int(count.strip())
    return result


def write_manifests(rows: list[dict[str, str]], out_dir: Path, seed: int) -> None:
    challenge_lines = [
        f"seed: {seed}",
        f"count: {len(rows)}",
        "",
        "file | source image | depth | puzzle type",
        "-" * 78,
    ]
    solution_lines = [
        f"seed: {seed}",
        f"count: {len(rows)}",
        "",
        "file | secret | source image | depth | puzzle type | solution path",
        "-" * 110,
    ]

    for row in rows:
        challenge_lines.append(
            f"{row['file']} | {row['source']} | {row['depth']} | {row['type']}"
        )
        solution_lines.append(
            f"{row['file']} | {row['secret']} | {row['source']} | "
            f"{row['depth']} | {row['type']} | {row['solution']}"
        )

    (out_dir / "challenges.txt").write_text(
        "\n".join(challenge_lines) + "\n", encoding="utf-8"
    )
    (out_dir / "solutions.txt").write_text(
        "\n".join(solution_lines) + "\n", encoding="utf-8"
    )


def generate(
    num_puzzles: dict[int, int],
    seed: int,
    src_dir: Path,
    out_dir: Path,
    clean: bool,
) -> None:
    rng = random.Random(seed)
    images = source_images(src_dir)
    templates = choose_templates(num_puzzles, rng)

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    for index, template in enumerate(templates, start=1):
        secret = secret_for(rng)
        source = rng.choice(images)

        with Image.open(source) as cover:
            puzzle, solution = template.build(cover.convert("RGB"), secret, rng)

        filename = f"puzzle_{index:04d}_depth{template.depth}.png"
        output = out_dir / filename
        save(puzzle, output, fmt="PNG")

        rows.append(
            {
                "file": str(output),
                "secret": secret,
                "source": str(source),
                "depth": str(template.depth),
                "type": template.name,
                "solution": solution,
            }
        )

    write_manifests(rows, out_dir, seed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--src-dir", type=Path, default=SRC_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument(
        "--num-puzzles",
        type=parse_num_puzzles,
        default=NUM_PUZZLES,
        help='Puzzle counts by depth, for example "1:5,2:5".',
    )
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    generate(
        num_puzzles=args.num_puzzles,
        seed=args.seed,
        src_dir=args.src_dir,
        out_dir=args.out_dir,
        clean=args.clean,
    )
    print(f"Wrote puzzles to {args.out_dir}")
    print(f"Public manifest: {args.out_dir / 'challenges.txt'}")
    print(f"Solution manifest: {args.out_dir / 'solutions.txt'}")


if __name__ == "__main__":
    main()
