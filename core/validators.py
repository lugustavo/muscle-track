import os

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image

VALID_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.webp']


def validate_avatar_image(image):
    """
    Validate avatar image extension, size and dimensions.

    Limits come from settings (AVATAR_MAX_SIZE_MB, AVATAR_MAX_WIDTH, ...).
    """
    ext = os.path.splitext(image.name)[1].lower()
    if ext not in VALID_EXTENSIONS:
        raise ValidationError(f'Formato de arquivo não suportado. Use: {", ".join(VALID_EXTENSIONS)}')

    max_size_bytes = settings.AVATAR_MAX_SIZE_MB * 1024 * 1024
    if image.size > max_size_bytes:
        raise ValidationError(
            f'O tamanho máximo permitido é {settings.AVATAR_MAX_SIZE_MB}MB. '
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

    if width > settings.AVATAR_MAX_WIDTH or height > settings.AVATAR_MAX_HEIGHT:
        raise ValidationError(
            f'As dimensões máximas permitidas são {settings.AVATAR_MAX_WIDTH}x{settings.AVATAR_MAX_HEIGHT}px. '
            f'Sua imagem tem {width}x{height}px.'
        )

    if width < settings.AVATAR_MIN_WIDTH or height < settings.AVATAR_MIN_HEIGHT:
        raise ValidationError(
            f'As dimensões mínimas permitidas são {settings.AVATAR_MIN_WIDTH}x{settings.AVATAR_MIN_HEIGHT}px. '
            f'Sua imagem tem {width}x{height}px.'
        )

    return image
