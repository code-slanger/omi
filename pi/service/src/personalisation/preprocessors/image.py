import io
from typing import Any


def preprocess(content: bytes, filename: str) -> dict[str, Any]:
    """
    Extract visual features from an image.
    Returns an embeddable text description + metadata.
    Requires: Pillow
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(content))
        width, height = img.size
        mode = img.mode

        palette = _dominant_colors(img.convert("RGB"), n=5)

        description = (
            f"Image '{filename}': {width}x{height}px, "
            f"dominant colours: {', '.join(palette)}"
        )

        metadata: dict[str, Any] = {
            "filename": filename,
            "media_type": "image",
            "width": width,
            "height": height,
            "mode": mode,
            "dominant_colours": ", ".join(palette),
        }

    except ImportError:
        description = f"Image file '{filename}' (install Pillow for analysis)"
        metadata = {"filename": filename, "media_type": "image"}
    except Exception as e:
        description = f"Image file '{filename}' (analysis failed: {e})"
        metadata = {"filename": filename, "media_type": "image"}

    return {"text": description, "metadata": metadata}


def _dominant_colors(img, n: int = 5) -> list[str]:
    """Return n dominant hex colours via quantisation."""
    small = img.resize((100, 100))
    quantised = small.quantize(colors=n, method=2).convert("RGB")

    counts: dict[tuple, int] = {}
    for pixel in quantised.getdata():
        counts[pixel] = counts.get(pixel, 0) + 1

    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]
    return [f"#{r:02x}{g:02x}{b:02x}" for (r, g, b), _ in top]
