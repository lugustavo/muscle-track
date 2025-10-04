import os
from django.core.exceptions import ValidationError
from django.conf import settings
from PIL import Image


def validate_avatar_image(image):
    """
    Validate avatar image size and dimensions
    """
    # Get settings from environment variables with defaults
    max_size_mb = int(os.getenv('AVATAR_MAX_SIZE_MB', 5))
    max_width = int(os.getenv('AVATAR_MAX_WIDTH', 2000))
    max_height = int(os.getenv('AVATAR_MAX_HEIGHT', 2000))
    min_width = int(os.getenv('AVATAR_MIN_WIDTH', 100))
    min_height = int(os.getenv('AVATAR_MIN_HEIGHT', 100))

    # Validate file size
    max_size_bytes = max_size_mb * 1024 * 1024  # Convert MB to bytes
    if image.size > max_size_bytes:
        raise ValidationError(f'O tamanho máximo permitido é {max_size_mb}MB. Seu arquivo tem {image.size / (1024 * 1024):.2f}MB.')

    # Validate image dimensions
    try:
        img = Image.open(image)
        width, height = img.size

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

    except Exception as e:
        raise ValidationError('Não foi possível processar a imagem. Certifique-se de que é um arquivo de imagem válido.')

    # Validate file extension
    valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
    ext = os.path.splitext(image.name)[1].lower()
    if ext not in valid_extensions:
        raise ValidationError(f'Formato de arquivo não suportado. Use: {", ".join(valid_extensions)}')

    return image
