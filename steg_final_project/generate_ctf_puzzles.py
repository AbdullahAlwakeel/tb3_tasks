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
OUT_DIR = Path("puzzle_datasets")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
BASE64_VARIANTS = (False, True)
ITERATIONS = 10
PAIRING_ATTEMPTS = 250


@dataclass(frozen=True)
class PuzzleTemplate:
    name: str
    depth: int
    build: Callable[[Image.Image, str, random.Random, bool], tuple[Image.Image, str]]


@dataclass(frozen=True)
class PuzzleVariant:
    template: PuzzleTemplate
    use_base64: bool

    @property
    def name(self) -> str:
        suffix = "base64" if self.use_base64 else "plain"
        return f"{self.template.name}_{suffix}"


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


def maybe_base64(use_base64: bool) -> str:
    return " -> base64 decode" if use_base64 else ""


def framed_step(use_base64: bool) -> str:
    return f"skip 4-byte length prefix{maybe_base64(use_base64)}"


def lsb_step(use_base64: bool) -> str:
    return f"zsteg b1,rgb,lsb,xy -> {framed_step(use_base64)}"


def alpha_step(use_base64: bool) -> str:
    return f"zsteg b1,a,lsb,xy -> {framed_step(use_base64)}"


def metadata_step(use_base64: bool) -> str:
    return f"exiftool ImageDescription -> base64 decode -> {framed_step(use_base64)}"


def append_step(use_base64: bool) -> str:
    return f"strings or inspect PNG trailer data -> {framed_step(use_base64)}"


def inner_png_step(use_base64: bool) -> str:
    return f"{framed_step(use_base64)} -> inner PNG"


def one_lsb(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    return (
        LSBEmbed.embed(text_to_bytes(secret), cover, use_base64=use_base64),
        lsb_step(use_base64),
    )


def one_alpha(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    return (
        AlphaEmbed.embed(text_to_bytes(secret), cover, use_base64=use_base64),
        alpha_step(use_base64),
    )


def one_append(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    return (
        AppendEmbed.embed(text_to_bytes(secret), cover, use_base64=use_base64),
        append_step(use_base64),
    )


def one_metadata(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    return (
        MetadataEmbed.embed(text_to_bytes(secret), cover, use_base64=use_base64),
        metadata_step(use_base64),
    )


def one_low_contrast(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    position = smooth_text_position(cover)
    return (
        LowContrastEmbed.embed(
            text_to_bytes(secret),
            cover,
            delta=8,
            position=position,
            font_size=36,
            wrap_width=60,
            use_base64=use_base64,
        ),
        "visually enhance local contrast or inspect manually near "
        f"position={position}{maybe_base64(use_base64)}",
    )


def inner_lsb_image(secret: str, rng: random.Random, use_base64: bool) -> Image.Image:
    return LSBEmbed.embed(
        text_to_bytes(secret),
        inner_canvas(secret, rng),
        use_base64=use_base64,
    )


def inner_alpha_image(secret: str, rng: random.Random, use_base64: bool) -> Image.Image:
    return AlphaEmbed.embed(
        text_to_bytes(secret),
        inner_canvas(secret, rng),
        use_base64=use_base64,
    )


def inner_metadata_image(secret: str, rng: random.Random, use_base64: bool) -> Image.Image:
    return MetadataEmbed.embed(
        text_to_bytes(secret),
        inner_canvas(secret, rng),
        use_base64=use_base64,
    )


def inner_low_contrast_image(secret: str, rng: random.Random, use_base64: bool) -> Image.Image:
    return LowContrastEmbed.embed(
        text_to_bytes(secret),
        inner_canvas(secret, rng),
        delta=8,
        position=(18, 72),
        font_size=36,
        wrap_width=60,
        use_base64=use_base64,
    )


def two_outer_lsb_inner_metadata(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    inner = inner_metadata_image(secret, rng, use_base64)
    outer = LSBEmbed.embed(image_to_bytes(inner), cover, use_base64=use_base64)
    return (
        outer,
        f"zsteg b1,rgb,lsb,xy -> {inner_png_step(use_base64)} -> "
        f"{metadata_step(use_base64)}",
    )


def two_outer_alpha_inner_metadata(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    inner = inner_metadata_image(secret, rng, use_base64)
    outer = AlphaEmbed.embed(image_to_bytes(inner), cover, use_base64=use_base64)
    return (
        outer,
        f"zsteg b1,a,lsb,xy -> {inner_png_step(use_base64)} -> "
        f"{metadata_step(use_base64)}",
    )


def two_outer_metadata_inner_lsb(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    inner = inner_lsb_image(secret, rng, use_base64)
    outer = MetadataEmbed.embed(image_to_bytes(inner), cover, use_base64=use_base64)
    return (
        outer,
        f"exiftool metadata -> base64 decode -> {inner_png_step(use_base64)} -> "
        f"{lsb_step(use_base64)}",
    )


def two_outer_append_inner_lsb(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    inner = inner_lsb_image(secret, rng, use_base64)
    outer = AppendEmbed.embed(image_to_bytes(inner), cover, use_base64=use_base64)
    return (
        outer,
        f"extract PNG trailer payload -> {inner_png_step(use_base64)} -> "
        f"{lsb_step(use_base64)}",
    )


def two_outer_lsb_inner_low_contrast(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    inner = inner_low_contrast_image(secret, rng, use_base64)
    outer = LSBEmbed.embed(image_to_bytes(inner), cover, use_base64=use_base64)
    return (
        outer,
        f"zsteg b1,rgb,lsb,xy -> {inner_png_step(use_base64)} -> "
        f"visually enhance/read text{maybe_base64(use_base64)}",
    )


def two_outer_alpha_inner_low_contrast(
    cover: Image.Image, secret: str, rng: random.Random, use_base64: bool
) -> tuple[Image.Image, str]:
    inner = inner_low_contrast_image(secret, rng, use_base64)
    outer = AlphaEmbed.embed(image_to_bytes(inner), cover, use_base64=use_base64)
    return (
        outer,
        f"zsteg b1,a,lsb,xy -> {inner_png_step(use_base64)} -> "
        f"visually enhance/read text{maybe_base64(use_base64)}",
    )


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


def puzzle_variants() -> list[PuzzleVariant]:
    return [
        PuzzleVariant(template=template, use_base64=use_base64)
        for template in TEMPLATES
        for use_base64 in BASE64_VARIANTS
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


def write_manifests(
    rows: list[dict[str, str]],
    out_dir: Path,
    seed: int,
    challenge_path: Path | None = None,
    solution_path: Path | None = None,
) -> None:
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

    challenge_path = challenge_path or out_dir / "challenges.txt"
    challenge_path.parent.mkdir(parents=True, exist_ok=True)
    challenge_path.write_text(
        "\n".join(challenge_lines) + "\n", encoding="utf-8"
    )
    solution_path = solution_path or out_dir / "solutions.txt"
    solution_path.parent.mkdir(parents=True, exist_ok=True)
    solution_path.write_text(
        "\n".join(solution_lines) + "\n", encoding="utf-8"
    )


def generate(
    num_puzzles: dict[int, int],
    seed: int,
    src_dir: Path,
    out_dir: Path,
    clean: bool,
    use_base64: bool = False,
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
            puzzle, solution = template.build(
                cover.convert("RGB"),
                secret,
                rng,
                use_base64,
            )

        filename = f"puzzle_{index:04d}.png"
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


def validate_full_dataset_inputs(images: list[Path], variants: list[PuzzleVariant]) -> None:
    if len(images) != len(variants):
        raise ValueError(
            "Full dataset generation expects exactly one source image per "
            f"puzzle variant. Found {len(images)} source images and "
            f"{len(variants)} variants."
        )


def build_paired_dataset(
    pairs: list[tuple[Path, PuzzleVariant]],
    out_dir: Path,
    rng: random.Random,
    seed: int,
    manifest_dir: Path | None = None,
    challenge_path: Path | None = None,
    solution_path: Path | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir = manifest_dir or out_dir
    rows: list[dict[str, str]] = []

    for index, (source, variant) in enumerate(pairs, start=1):
        secret = secret_for(rng)

        with Image.open(source) as cover:
            puzzle, solution = variant.template.build(
                cover.convert("RGB"),
                secret,
                rng,
                variant.use_base64,
            )

        filename = f"puzzle_{index:04d}.png"
        output = out_dir / filename
        manifest_output = manifest_dir / filename
        save(puzzle, output, fmt="PNG")

        rows.append(
            {
                "file": str(manifest_output),
                "secret": secret,
                "source": str(source),
                "depth": str(variant.template.depth),
                "type": variant.name,
                "solution": solution,
            }
        )

    write_manifests(
        rows,
        out_dir,
        seed,
        challenge_path=challenge_path,
        solution_path=solution_path,
    )


def generate_puzzle_datasets(
    seed: int,
    src_dir: Path,
    out_dir: Path,
    iterations: int = ITERATIONS,
    clean: bool = False,
) -> None:
    if iterations < 1:
        raise ValueError(f"iterations must be >= 1, got {iterations}.")

    rng = random.Random(seed)
    images = source_images(src_dir)
    variants = puzzle_variants()
    validate_full_dataset_inputs(images, variants)

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    challenges_dir = out_dir / "challenges"
    solutions_dir = out_dir / "solutions"
    challenges_dir.mkdir(parents=True, exist_ok=True)
    solutions_dir.mkdir(parents=True, exist_ok=True)

    for iteration in range(1, iterations + 1):
        iteration_seed = rng.getrandbits(64)
        iteration_rng = random.Random(iteration_seed)
        iteration_dir = out_dir / str(iteration)
        attempt_dir = out_dir / f".{iteration}.tmp"

        if iteration_dir.exists():
            raise FileExistsError(
                f"{iteration_dir} already exists. Use --clean to replace it."
            )

        last_error: Exception | None = None
        for _ in range(PAIRING_ATTEMPTS):
            shuffled_images = images[:]
            shuffled_variants = variants[:]
            iteration_rng.shuffle(shuffled_images)
            iteration_rng.shuffle(shuffled_variants)
            pairs = list(zip(shuffled_images, shuffled_variants))

            if attempt_dir.exists():
                shutil.rmtree(attempt_dir)

            try:
                build_paired_dataset(
                    pairs=pairs,
                    out_dir=attempt_dir,
                    rng=iteration_rng,
                    seed=iteration_seed,
                    manifest_dir=iteration_dir,
                    challenge_path=challenges_dir / f"challenges_{iteration:02d}.txt",
                    solution_path=solutions_dir / f"solutions_{iteration:02d}.txt",
                )
            except ValueError as exc:
                last_error = exc
                shutil.rmtree(attempt_dir, ignore_errors=True)
                continue

            attempt_dir.rename(iteration_dir)
            break
        else:
            raise RuntimeError(
                "Could not find a valid random image/template pairing for "
                f"iteration {iteration} after {PAIRING_ATTEMPTS} attempts."
            ) from last_error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--src-dir", type=Path, default=SRC_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument(
        "--iterations",
        type=int,
        default=ITERATIONS,
        help="Number of complete puzzle-set folders to generate.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the output directory before generating new files.",
    )
    args = parser.parse_args()

    generate_puzzle_datasets(
        seed=args.seed,
        src_dir=args.src_dir,
        out_dir=args.out_dir,
        iterations=args.iterations,
        clean=args.clean,
    )
    print(f"Wrote {args.iterations} puzzle datasets to {args.out_dir}")


if __name__ == "__main__":
    main()
