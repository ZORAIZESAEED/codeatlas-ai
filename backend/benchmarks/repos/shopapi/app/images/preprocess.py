"""Image utilities used before thumbnails are stored or images are sent to the classifier."""
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.config import get_settings

TARGET_SIZE = (224, 224)


def resize_image(image: Image.Image, size=TARGET_SIZE) -> Image.Image:
    """Scale an image to the target size, keeping its aspect ratio and padding the rest."""
    image = image.copy()
    image.thumbnail(size)
    canvas = Image.new("RGB", size, (255, 255, 255))
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def to_grayscale(image: Image.Image) -> Image.Image:
    return image.convert("L")


def normalize_pixels(image: Image.Image) -> list[float]:
    """Convert pixel values from 0-255 to the 0.0-1.0 range expected by the model."""
    return [value / 255.0 for value in image.getdata()]


def preprocess_for_model(data: bytes) -> list[float]:
    """Full pipeline: decode bytes, resize, convert to grayscale and normalize."""
    image = Image.open(BytesIO(data))
    return normalize_pixels(to_grayscale(resize_image(image)))


def save_product_image(product_id: int, data: bytes) -> str:
    settings = get_settings()
    target = Path(settings.upload_dir) / f"product-{product_id}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    resize_image(Image.open(BytesIO(data)), (512, 512)).save(target)
    return str(target)
