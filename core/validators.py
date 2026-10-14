import os

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image

VALID_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.webp']


def _validate_image(image, *, max_size_mb, max_width, max_height, min_width, min_height):
    ext = os.path.splitext(image.name)[1].lower()
    if ext not in VALID_EXTENSIONS:
        raise ValidationError(f'Formato de arquivo não suportado. Use: {", ".join(VALID_EXTENSIONS)}')

    max_size_bytes = max_size_mb * 1024 * 1024
    if image.size > max_size_bytes:
        raise ValidationError(
            f'O tamanho máximo permitido é {max_size_mb}MB. '
            f'Seu arquivo tem {image.size / (1024 * 1024):.2f}MB.'
        )

    try:
        with Image.open(image) as img:
            img.verify()
        image.seek(0)
        with Image.open(image) as img:
            width, height = img.size
    except Exception:
        raise ValidationError('Não foi possível processar a imagem. Certifique-se de que é um arquivo de imagem válido.')
    finally:
        image.seek(0)

    if width > max_width or height > max_height:
        raise ValidationError(
            f'As dimensões máximas permitidas são {max_width}x{max_height}px. '
            f'Sua imagem tem {width}x{height}px.'
        )

    if width < min_width or height < min_height:
        raise ValidationError(
            f'As dimensões mínimas permitidas são {min_width}x{min_height}px. '
            f'Sua imagem tem {width}x{height}px.'
        )

    return image


def validate_avatar_image(image):
    """Avatar limits come from settings (AVATAR_MAX_SIZE_MB, AVATAR_MAX_WIDTH, ...)."""
    return _validate_image(
        image,
        max_size_mb=settings.AVATAR_MAX_SIZE_MB,
        max_width=settings.AVATAR_MAX_WIDTH, max_height=settings.AVATAR_MAX_HEIGHT,
        min_width=settings.AVATAR_MIN_WIDTH, min_height=settings.AVATAR_MIN_HEIGHT,
    )


def validate_photo_image(image):
    """Progress photos are straight from a phone camera: allow larger files than avatars."""
    return _validate_image(
        image,
        max_size_mb=settings.PHOTO_MAX_SIZE_MB,
        max_width=settings.PHOTO_MAX_DIMENSION, max_height=settings.PHOTO_MAX_DIMENSION,
        min_width=100, min_height=100,
    )
