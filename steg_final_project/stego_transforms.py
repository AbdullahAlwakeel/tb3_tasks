"""
stego_transforms.py
===================
Five steganographic embedding techniques plus an XOR image transform.
Embedding techniques handle both text and image payloads through a unified
bytes interface.

All embed/extract pairs are byte-level: the caller decides whether those bytes
represent text or an image. Helper functions handle the conversion.

Techniques
----------
1. LSBEmbed           hide bytes in the LSBs of RGB pixel values
2. AlphaEmbed         hide bytes in the LSBs of the alpha channel
3. AppendEmbed        append bytes after the PNG/JPEG EOF marker
4. MetadataEmbed      hide bytes in EXIF ImageDescription
5. LowContrastEmbed   render text visually on the image at near-background colour
6. XOR                XOR image pixels with a text, bytes, or image key

Payload helpers
---------------
  text_to_bytes / bytes_to_text
  image_to_bytes / bytes_to_image

Requirements
------------
    pip install Pillow numpy piexif
"""

from __future__ import annotations

import io
import base64
import struct
import textwrap
from pathlib import Path

import numpy as np
import piexif
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
import PIL, PIL.PngImagePlugin, PIL.JpegImagePlugin

# ---------------------------------------------------------------------------
# Payload helpers — the bridge between "what you want to hide" and raw bytes
# ---------------------------------------------------------------------------

def text_to_bytes(text: str) -> bytes:
    """Encode a UTF-8 string to bytes."""
    return text.encode("utf-8")


def bytes_to_text(data: bytes) -> str:
    """Decode bytes to a UTF-8 string."""
    return data.decode("utf-8")


def image_to_bytes(image: Image.Image, fmt: str = "PNG") -> bytes:
    """Serialise a PIL Image to a bytes object (default PNG for losslessness)."""
    buf = io.BytesIO()
    kwargs: dict = {}
    if "exif" in image.info and fmt.upper() in ("JPEG", "JPG", "PNG"):
        kwargs["exif"] = image.info["exif"]
    image.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def bytes_to_image(data: bytes) -> Image.Image:
    """Deserialise a bytes object back to a PIL Image."""
    return Image.open(io.BytesIO(data))


def _load_readable_font(font_size: int) -> ImageFont.ImageFont:
    for font_path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(font_path, font_size)
        except Exception:
            pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Internal byte-framing helpers
# Techniques that store variable-length payloads need a length prefix so the
# decoder knows where the payload ends.
# ---------------------------------------------------------------------------

#ONLY non-empty for automatic testing. Set to empty string when actually encoding, to prevent statistical shift

_SENTINEL = b""   # 4-byte magic marker


def _frame(payload: bytes) -> bytes:
    """Prepend SENTINEL + 4-byte big-endian length."""
    return _SENTINEL + struct.pack(">I", len(payload)) + payload


def _unframe(data: bytes) -> bytes:
    """
    Find SENTINEL in data, read the length prefix, return the payload.
    Raises ValueError if the sentinel is not found.
    """
    idx = data.find(_SENTINEL)
    if idx == -1:
        raise ValueError("Sentinel not found — payload may be absent or corrupted.")
    start  = idx + len(_SENTINEL)
    length = struct.unpack(">I", data[start : start + 4])[0]
    return data[start + 4 : start + 4 + length]


def _payload_for_embed(payload: bytes, use_base64: bool) -> bytes:
    """
    Optionally add a beginner-readable base64 layer before the stego technique.

    Extraction intentionally returns these bytes unchanged. Callers that set
    use_base64=True should expect one extra base64 decode after extracting and
    unframing the hidden payload.
    """
    if use_base64:
        return base64.b64encode(payload)
    return payload


# ===========================================================================
# 1. LSBEmbed
# ===========================================================================

class LSBEmbed:
    """
    Hide payload bytes in the least-significant bit of each RGB channel value,
    scanning pixels left-to-right, top-to-bottom, R→G→B per pixel.

    Capacity (bytes) = floor(width * height * 3 / 8) - 9
                       (minus 9 for the 4-byte sentinel + 4-byte length prefix,
                        rounded down for the null byte)

    Works with text payload:
        embed(text_to_bytes("hello"), cover) → stego
        bytes_to_text(extract(stego))        → "hello"

    Works with image payload:
        embed(image_to_bytes(secret_img), cover) → stego
        bytes_to_image(extract(stego))           → secret_img
    """

    @staticmethod
    def capacity(image: Image.Image) -> int:
        """Maximum payload bytes this image can carry."""
        w, h = image.size
        return (w * h * 3) // 8 - 9

    @staticmethod
    def embed(
        payload: bytes,
        cover: Image.Image,
        use_base64: bool = False,
    ) -> Image.Image:
        img     = cover.convert("RGB")
        pixels  = np.array(img, dtype=np.uint8)
        payload = _payload_for_embed(payload, use_base64)
        framed  = _frame(payload)

        if len(framed) * 8 > pixels.size:
            raise ValueError(
                f"Payload too large: need {len(framed)*8} bits, "
                f"image has {pixels.size} channels."
            )

        bits = "".join(format(b, "08b") for b in framed)
        flat = pixels.flatten()
        for i, bit in enumerate(bits):
            flat[i] = (flat[i] & 0xFE) | int(bit)

        result = Image.fromarray(flat.reshape(pixels.shape))
        result.info.update(cover.info)
        return result

    @staticmethod
    def extract(stego: Image.Image) -> bytes:
        flat = np.array(stego.convert("RGB")).flatten()
        bits = "".join(str(int(v) & 1) for v in flat)
        raw  = bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))
        return _unframe(raw)


# ===========================================================================
# 2. AlphaEmbed
# ===========================================================================

class AlphaEmbed:
    """
    Hide payload bytes in the LSBs of the alpha (transparency) channel.
    All alpha values are pinned to 255 (fully opaque) before embedding so
    the image appears unchanged regardless of original transparency.

    Output must be saved as PNG to preserve the alpha channel.

    Capacity (bytes) = floor(width * height / 8) - 9

    Works with text or image payloads identically to LSBEmbed.
    """

    @staticmethod
    def capacity(image: Image.Image) -> int:
        w, h = image.size
        return (w * h) // 8 - 9

    @staticmethod
    def embed(
        payload: bytes,
        cover: Image.Image,
        use_base64: bool = False,
    ) -> Image.Image:
        img    = cover.convert("RGBA")
        r, g, b, a = img.split()
        a_arr  = np.array(a, dtype=np.uint8)
        payload = _payload_for_embed(payload, use_base64)
        framed = _frame(payload)

        flat = a_arr.flatten()
        if len(framed) * 8 > len(flat):
            raise ValueError("Payload too large for alpha channel.")

        flat[:] = 255  # baseline: fully opaque
        bits = "".join(format(b_, "08b") for b_ in framed)
        for i, bit in enumerate(bits):
            flat[i] = (flat[i] & 0xFE) | int(bit)

        new_alpha = Image.fromarray(flat.reshape(a_arr.shape), mode="L")
        result    = Image.merge("RGBA", (r, g, b, new_alpha))
        result.info.update(cover.info)
        return result

    @staticmethod
    def extract(stego: Image.Image) -> bytes:
        a    = np.array(stego.convert("RGBA"))[:, :, 3].flatten()
        bits = "".join(str(int(v) & 1) for v in a)
        raw  = bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))
        return _unframe(raw)


# ===========================================================================
# 3. AppendEmbed
# ===========================================================================

class AppendEmbed:
    """
    Appends framed payload bytes after the image's EOF marker.
    The image renders identically; the payload is invisible to any viewer.

    Because PIL Image objects don't carry post-EOF bytes natively, we use two
    strategies:
      - In-memory:  store in image.info["_append_payload"] for pipeline use.
      - On-disk:    save_to_file() / load_from_file() write/read the raw bytes.

    Works with text or image payloads.
    """

    @staticmethod
    def embed(
        payload: bytes,
        cover: Image.Image,
        use_base64: bool = False,
    ) -> Image.Image:
        payload = _payload_for_embed(payload, use_base64)
        result = cover.copy()
        result.info.update(cover.info)
        result.info["_append_payload"] = _frame(payload)
        return result

    @staticmethod
    def extract(stego: Image.Image) -> bytes:
        raw = stego.info.get("_append_payload", b"")
        if not raw:
            raise ValueError("No appended payload found in image.info.")
        return _unframe(raw)

    @staticmethod
    def save_to_file(stego: Image.Image, path: str | Path, fmt: str = "PNG") -> None:
        """Save image to disk, physically appending the payload after EOF."""
        path = Path(path)
        stego.save(path, format=fmt)
        payload = stego.info.get("_append_payload", b"")
        if payload:
            with open(path, "ab") as f:
                f.write(payload)

    @staticmethod
    def load_from_file(path: str | Path) -> Image.Image:
        """Load image from disk, restoring the appended payload into image.info."""
        path  = Path(path)
        data  = path.read_bytes()
        img   = Image.open(io.BytesIO(data))
        img.load()
        idx   = data.rfind(_SENTINEL)
        if idx != -1:
            img.info["_append_payload"] = data[idx:]
        return img


# ===========================================================================
# 4. MetadataEmbed
# ===========================================================================

class MetadataEmbed:
    """
    Hides the payload in the EXIF ImageDescription tag.

    Bytes are base64-encoded before storage so arbitrary binary (including
    embedded image data) survives EXIF's text field cleanly.

    Best preserved in JPEG or PNG with EXIF support.
    Works with text or image payloads.
    """

    @staticmethod
    def embed(
        payload: bytes,
        cover: Image.Image,
        use_base64: bool = False,
    ) -> Image.Image:
        payload = _payload_for_embed(payload, use_base64)
        encoded = base64.b64encode(_frame(payload))

        img = cover.copy().convert("RGB")
        try:
            exif_dict = piexif.load(cover.info.get("exif", b""))
        except Exception:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}

        exif_dict.setdefault("0th", {})[piexif.ImageIFD.ImageDescription] = encoded
        img.info["exif"] = piexif.dump(exif_dict)
        return img

    @staticmethod
    def extract(stego: Image.Image) -> bytes:
        exif_bytes = stego.info.get("exif", b"")
        if not exif_bytes:
            raise ValueError("No EXIF data found.")
        exif_dict = piexif.load(exif_bytes)
        raw = exif_dict.get("0th", {}).get(piexif.ImageIFD.ImageDescription, b"")
        if not raw:
            raise ValueError("No payload in EXIF ImageDescription.")
        return _unframe(base64.b64decode(raw))


# ===========================================================================
# 5. LowContrastEmbed   (text only)
# ===========================================================================
class LowContrastEmbed:
    """
    Renders secret text directly onto the image in a colour only marginally
    different from the background — invisible to casual inspection, revealed
    by contrast stretching.
 
    TEXT ONLY: the payload must be a UTF-8 string encoded as bytes.
               Image payloads are not supported because this technique renders
               human-readable glyphs, not binary data.
 
    Parameters
    ----------
    delta     : how many RGB steps to lighten/darken each covered image pixel.
                Smaller = harder to see; larger = easier to reveal.
    position  : (x, y) top-left pixel of the text block.
    font_size : point size (uses PIL default if a TTF is unavailable).
    wrap_width: character width for line-wrapping.
 
    Reveal with:
        LowContrastEmbed.reveal(stego)  →  high-contrast PIL Image
    """
 
    @staticmethod
    def embed(
        payload: bytes,
        cover: Image.Image,
        delta:      int                  = 20,
        position:   tuple[int, int]      = (10, 10),
        font_size:  int                  = 18,
        wrap_width: int                  = 60,
        use_base64: bool                 = False,
    ) -> Image.Image:
        payload = _payload_for_embed(payload, use_base64)
        text = payload.decode("utf-8")   # raises cleanly if payload is binary
 
        if delta < 1:
            raise ValueError("delta must be at least 1.")

        img = cover.convert("RGB").copy()
 
        font = _load_readable_font(font_size)
 
        wrapped = textwrap.fill(text, width=wrap_width)

        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).text(position, wrapped, fill=255, font=font)

        pixels = np.array(img, dtype=np.int16)
        mask_arr = np.array(mask, dtype=np.float32) / 255.0
        covered = mask_arr > 0

        luminance = (
            0.299 * pixels[:, :, 0]
            + 0.587 * pixels[:, :, 1]
            + 0.114 * pixels[:, :, 2]
        )
        direction = np.where(luminance >= 128, -1, 1).astype(np.int16)
        amount = np.ceil(delta * mask_arr).astype(np.int16)

        adjustment = (direction * amount)[:, :, None]
        pixels[covered] = np.clip(pixels[covered] + adjustment[covered], 0, 255)

        result = Image.fromarray(pixels.astype(np.uint8), mode="RGB")
        result.info.update(cover.info)
        return result
 
    @staticmethod
    def extract(stego: Image.Image) -> bytes:
        """
        Low contrast hiding is NOT reversible by code — the text pixels are
        indistinguishable from cover pixels once merged.
 
        Use reveal() to produce a human-readable high-contrast image, then
        read the text visually or via OCR.
        """
        raise NotImplementedError(
            "LowContrastEmbed is not programmatically reversible. "
            "Use LowContrastEmbed.reveal() to produce a contrast-boosted image "
            "and read the text visually or with an OCR library."
        )
 
    @staticmethod
    def reveal(
        stego: Image.Image,
        blur_radius: float = 2.0,
        strength: float = 8.0,
    ) -> Image.Image:
        """
        Return a blind local-contrast enhancement of the stego image.

        The hidden text only changes nearby pixels by a small delta, so global
        contrast stretching often has no visible effect. This estimates the local
        background with a blur, then amplifies each pixel's difference from that
        local background. It does not require or store the original cover image.
        """
        if blur_radius <= 0:
            raise ValueError("blur_radius must be positive.")
        if strength < 0:
            raise ValueError("strength must be non-negative.")

        img = stego.convert("RGB")
        background = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))

        pixels = np.array(img, dtype=np.float32)
        bg = np.array(background, dtype=np.float32)
        detail = pixels - bg
        enhanced = np.clip(pixels + strength * detail, 0, 255).astype(np.uint8)

        result = Image.fromarray(enhanced, mode="RGB")
        result.info.update(stego.info)
        return result
 


# ===========================================================================
# 6. XOR
# ===========================================================================

XORKey = str | bytes | Image.Image


def _xor_key_bytes(key: XORKey, mode: str) -> bytes:
    """Convert a text, bytes, or image key into bytes for a repeating XOR stream."""
    if isinstance(key, str):
        data = key.encode("utf-8")
    elif isinstance(key, bytes):
        data = key
    elif isinstance(key, Image.Image):
        data = key.convert(mode).tobytes()
    else:
        raise TypeError("XOR key must be str, bytes, or PIL.Image.Image.")

    if not data:
        raise ValueError("XOR key must be non-empty.")
    return data


def XOR(image: Image.Image, key: XORKey) -> Image.Image:
    """
    XOR every pixel channel in image with a repeating key stream.

    key can be:
      - str: encoded as UTF-8 bytes
      - bytes: used directly
      - PIL Image: converted to the input image mode and used as key pixels

    The returned image can be serialized with image_to_bytes(...) and embedded
    with LSBEmbed, or passed into any other image transform. XOR is symmetric:
    applying XOR(result, key) recovers the original image.
    """
    img = image.copy()
    pixels = np.array(img, dtype=np.uint8)
    key_bytes = _xor_key_bytes(key, img.mode)
    key_stream = np.resize(np.frombuffer(key_bytes, dtype=np.uint8), pixels.size)
    xored = np.bitwise_xor(pixels.flatten(), key_stream).reshape(pixels.shape)

    result = Image.fromarray(xored.astype(np.uint8), mode=img.mode)
    result.info.update(image.info)
    return result


# ===========================================================================
# Unified save / load  (preserves EXIF and appended data across all techniques)
# ===========================================================================

def save(image: Image.Image, path: str | Path, fmt: str = "PNG") -> None:
    """
    Save a stego image correctly for any technique:
      - Writes EXIF bytes if present (MetadataEmbed)
      - Physically appends payload bytes if present (AppendEmbed)
    """
    path = Path(path)
    fmt  = fmt.upper()
    kwargs: dict = {}
    if "exif" in image.info and fmt in ("JPEG", "JPG", "PNG"):
        kwargs["exif"] = image.info["exif"]
    image.save(path, format=fmt, **kwargs)

    tail = image.info.get("_append_payload", b"")
    if tail:
        with open(path, "ab") as f:
            f.write(tail)


def load(path: str | Path) -> Image.Image:
    """
    Load a stego image, restoring:
      - EXIF bytes (available via image.info["exif"])
      - Appended payload (restored to image.info["_append_payload"])
    """
    path = Path(path)
    data = path.read_bytes()
    img  = Image.open(io.BytesIO(data))
    img.load()
    idx  = data.rfind(_SENTINEL)
    if idx != -1:
        img.info["_append_payload"] = data[idx:]
    return img


# ===========================================================================
# Quick self-test
# ===========================================================================

def _pixels_equal(a: Image.Image, b: Image.Image) -> bool:
    return np.array_equal(np.array(a), np.array(b))


def _run_tests():
    import matplotlib.pyplot as plt
    global _SENTINEL
    _SENTINEL = b"f7b4" #set to non-empty value
    rng = np.random.default_rng(0)

    # --- cover images (sized for technique capacity) ---
    # LSB/Append/Metadata need enough pixels for an embedded PNG image.
    # Alpha channel has 1/3 the capacity of LSB so needs a larger cover.
    #cover_large = Image.fromarray(
    #    rng.integers(100, 200, (600, 800, 3), dtype=np.uint8), "RGB"
    #)
    cover_large = Image.open("src_images/reflect_sea.jpg")
    #cover_large = Image.open("src_images/Bliss_(Windows_XP).png")
    cover_alpha = cover_large.convert("RGBA")
    #white       = Image.fromarray(np.full((300, 400, 3), 245, dtype=np.uint8), "RGB")
    #white = Image.open("src_images/Bliss_(Windows_XP).png")
    # --- payloads ---
    text_msg   = "Firm resolutions happen in proportion to the resolute, and noble deeds come in proportion to the noble."
    text_bytes = text_to_bytes(text_msg)

    # Small secret image that fits in all channel capacities
    secret_img = Image.fromarray(
        rng.integers(0, 255, (30, 40, 3), dtype=np.uint8), "RGB"
    )
    img_bytes  = image_to_bytes(secret_img)

    results: list[tuple[str, bool, str]] = []

    def check(name, ok, note=""):
        results.append((name, ok, note))

    # 1. LSBEmbed – text
    stego = LSBEmbed.embed(text_bytes, cover_large)
    check("LSBEmbed/text", bytes_to_text(LSBEmbed.extract(stego)) == text_msg)
    #plt.imshow(stego)
    #plt.show()

    # 2. LSBEmbed – image
    stego     = LSBEmbed.embed(img_bytes, cover_large)
    recovered = bytes_to_image(LSBEmbed.extract(stego))
    check("LSBEmbed/image", _pixels_equal(recovered, secret_img))
    #plt.imshow(stego)
    #plt.show()

    # 3. AlphaEmbed – text
    stego = AlphaEmbed.embed(text_bytes, cover_alpha)
    check("AlphaEmbed/text", bytes_to_text(AlphaEmbed.extract(stego)) == text_msg)
    #plt.imshow(stego)
    #plt.show()

    # 4. AlphaEmbed – image
    stego     = AlphaEmbed.embed(img_bytes, cover_alpha)
    recovered = bytes_to_image(AlphaEmbed.extract(stego))
    check("AlphaEmbed/image", _pixels_equal(recovered, secret_img))
    #plt.imshow(stego)
    #plt.show()

    # 5. AppendEmbed – text
    stego = AppendEmbed.embed(text_bytes, cover_large)
    check("AppendEmbed/text", bytes_to_text(AppendEmbed.extract(stego)) == text_msg)
    #plt.imshow(stego)
    #plt.show()

    # 6. AppendEmbed – image
    stego     = AppendEmbed.embed(img_bytes, cover_large)
    recovered = bytes_to_image(AppendEmbed.extract(stego))
    check("AppendEmbed/image", _pixels_equal(recovered, secret_img))
    #plt.imshow(stego)
    #plt.show()

    # 7. MetadataEmbed – text
    stego = MetadataEmbed.embed(text_bytes, cover_large)
    check("MetadataEmbed/text", bytes_to_text(MetadataEmbed.extract(stego)) == text_msg)
    #plt.imshow(stego)
    #plt.show()

    # 8. MetadataEmbed – image
    stego     = MetadataEmbed.embed(img_bytes, cover_large)
    recovered = bytes_to_image(MetadataEmbed.extract(stego))
    check("MetadataEmbed/image", _pixels_equal(recovered, secret_img))
    #plt.imshow(stego)
    #plt.show()

    # 9. LowContrastEmbed – text only (not programmatically reversible)
    stego    = LowContrastEmbed.embed(text_bytes, cover_large, delta=20)
    revealed = LowContrastEmbed.reveal(stego)
    
    print("SHOWING LOW CONTRAST EMBED")
    plt.imshow(stego)
    plt.show()
    plt.imshow(revealed)
    plt.show()
    check(
        "LowContrastEmbed/reveal_returns_image",
        isinstance(revealed, Image.Image) and not _pixels_equal(revealed, stego.convert("RGB")),
    )

    # 10. XOR – text key
    xored = XOR(secret_img, "hunter2")
    recovered = XOR(xored, "hunter2")
    check("XOR/text_key", _pixels_equal(recovered, secret_img))
    plt.imshow(xored)
    plt.show()

    # 11. XOR – image key, then embed XOR output with LSB
    key_img = Image.fromarray(
        rng.integers(0, 255, secret_img.size[::-1] + (3,), dtype=np.uint8), "RGB"
    )
    xored = XOR(secret_img, key_img)
    stego = LSBEmbed.embed(image_to_bytes(xored), cover_large)
    extracted_xored = bytes_to_image(LSBEmbed.extract(stego))
    recovered = XOR(extracted_xored, key_img)
    check("XOR/image_key_then_LSB", _pixels_equal(recovered, secret_img))
    plt.imshow(stego)
    plt.show()

    # 12. AppendEmbed – file round-trip
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        stego = AppendEmbed.embed(text_bytes, cover_large)
        AppendEmbed.save_to_file(stego, tmp)
        loaded = AppendEmbed.load_from_file(tmp)
        check("AppendEmbed/file_roundtrip",
              bytes_to_text(AppendEmbed.extract(loaded)) == text_msg)
    finally:
        os.unlink(tmp)

    # Report
    pad = max(len(n) for n, *_ in results)
    print(f"\n{'Test':<{pad}}  Result")
    print("-" * (pad + 10))
    for name, ok, note in results:
        status = "PASS ✓" if ok else f"FAIL ✗  {note}"
        print(f"{name:<{pad}}  {status}")
    print()
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} tests passed.")


if __name__ == "__main__":
    _run_tests()
