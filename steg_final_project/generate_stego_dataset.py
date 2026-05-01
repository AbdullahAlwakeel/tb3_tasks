"""
Generate reproducible stego images from src_images.

Each generated image receives exactly one generated secret of the form
secret{XXXXXXXX}, where X is a random hexadecimal digit. The output manifest
records the secret, source image, output image, and transform sequence.
"""

from __future__ import annotations

import argparse
import itertools
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from stego_transforms import (
    AlphaEmbed,
    AppendEmbed,
    LSBEmbed,
    LowContrastEmbed,
    MetadataEmbed,
    #XOR,
    save,
    text_to_bytes,
)


# Number of images to create at each transform depth.
# Example: {1: 3, 2: 4} creates 3 single-transform images and 4 two-transform
# images. Each chosen transform sequence is unique.
NUM_TRANSFORMS = {1: 3, 2: 4}

SRC_DIR = Path("src_images")
OUT_DIR = Path("generated_stego")
MANIFEST_NAME = "manifest.txt"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class Pipeline:
    names: tuple[str, ...]


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


def possible_pipelines(max_depth: int) -> dict[int, list[Pipeline]]:
    """
    Build unique, safe transform sequences.

    The order is intentionally fixed. Pixel-changing transforms run before
    metadata/tail transforms, and LSB runs before Alpha so RGB data is not
    discarded by a later RGB conversion.
    """
    ordered_transforms = (
        #"XOR",
        "LowContrastEmbed",
        "LSBEmbed",
        "AlphaEmbed",
        "MetadataEmbed",
        "AppendEmbed",
    )
    embedders = {
        "LowContrastEmbed",
        "LSBEmbed",
        "AlphaEmbed",
        "MetadataEmbed",
        "AppendEmbed",
    }

    by_depth: dict[int, list[Pipeline]] = {}
    for depth in range(1, max_depth + 1):
        pipelines = []
        for names in itertools.combinations(ordered_transforms, depth):
            if any(name in embedders for name in names):
                pipelines.append(Pipeline(names=names))
        by_depth[depth] = pipelines
    return by_depth


def choose_pipelines(
    num_transforms: dict[int, int],
    rng: random.Random,
) -> list[Pipeline]:
    max_depth = max(num_transforms, default=0)
    available = possible_pipelines(max_depth)
    selected: list[Pipeline] = []

    for depth, count in sorted(num_transforms.items()):
        if depth < 1:
            raise ValueError(f"Transform depth must be >= 1, got {depth}.")
        choices = available.get(depth, [])
        if count > len(choices):
            raise ValueError(
                f"Requested {count} images at depth {depth}, but only "
                f"{len(choices)} unique transform sequences are available."
            )
        selected.extend(rng.sample(choices, count))

    return selected


def apply_pipeline(
    image: Image.Image,
    secret: str,
    pipeline: Pipeline,
    rng: random.Random,
) -> tuple[Image.Image, list[str]]:
    payload = text_to_bytes(secret)
    result = image.convert("RGB")
    details: list[str] = []

    for name in pipeline.names:
        if name == "XOR":
            key = f"xor-key-{rng.getrandbits(64):016x}"
            result = XOR(result, key)
            details.append(f"XOR(key={key})")
        elif name == "LowContrastEmbed":
            result = LowContrastEmbed.embed(
                payload,
                result,
                delta=5,
                position=(10, 10),
                font_size=24,
                wrap_width=60,
            )
            details.append("LowContrastEmbed(delta=5, position=(10,10), font_size=24)")
        elif name == "LSBEmbed":
            result = LSBEmbed.embed(payload, result)
            details.append("LSBEmbed")
        elif name == "AlphaEmbed":
            result = AlphaEmbed.embed(payload, result)
            details.append("AlphaEmbed")
        elif name == "MetadataEmbed":
            result = MetadataEmbed.embed(payload, result)
            details.append("MetadataEmbed")
        elif name == "AppendEmbed":
            result = AppendEmbed.embed(payload, result)
            details.append("AppendEmbed")
        else:
            raise ValueError(f"Unknown transform: {name}")

    return result, details


def write_manifest(rows: list[dict[str, str]], manifest_path: Path, seed: int) -> None:
    lines = [
        f"seed: {seed}",
        f"count: {len(rows)}",
        "",
        "secret | source image | output image | transformations",
        "-" * 78,
    ]
    for row in rows:
        lines.append(
            f"{row['secret']} | {row['source']} | {row['output']} | "
            f"{row['transforms']}"
        )
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate(
    num_transforms: dict[int, int],
    seed: int,
    src_dir: Path,
    out_dir: Path,
    clean: bool = False,
) -> Path:
    rng = random.Random(seed)
    images = source_images(src_dir)
    pipelines = choose_pipelines(num_transforms, rng)

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    for index, pipeline in enumerate(pipelines, start=1):
        secret = secret_for(rng)
        source = rng.choice(images)

        with Image.open(source) as img:
            stego, details = apply_pipeline(img, secret, pipeline, rng)

        output_name = f"stego_{index:04d}_depth{len(pipeline.names)}.png"
        output_path = out_dir / output_name
        save(stego, output_path, fmt="PNG")

        rows.append(
            {
                "secret": secret,
                "source": str(source),
                "output": str(output_path),
                "transforms": " -> ".join(details),
            }
        )

    manifest_path = out_dir / MANIFEST_NAME
    write_manifest(rows, manifest_path, seed)
    return manifest_path


def parse_num_transforms(value: str) -> dict[int, int]:
    """
    Parse a compact depth spec such as "1:3,2:4".
    """
    result: dict[int, int] = {}
    for item in value.split(","):
        depth, count = item.split(":", 1)
        result[int(depth.strip())] = int(count.strip())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--src-dir", type=Path, default=SRC_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument(
        "--num-transforms",
        type=parse_num_transforms,
        default=NUM_TRANSFORMS,
        help='Depth counts like "1:3,2:4".',
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the output directory before generating new files.",
    )
    args = parser.parse_args()

    manifest = generate(
        num_transforms=args.num_transforms,
        seed=args.seed,
        src_dir=args.src_dir,
        out_dir=args.out_dir,
        clean=args.clean,
    )
    print(f"Wrote manifest: {manifest}")


if __name__ == "__main__":
    main()
