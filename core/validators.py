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


# ---- Contact data (shared by the trainer's and the student's forms) ----

BR_STATES = {
    'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG', 'PA',
    'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO',
}
MAX_AGE_YEARS = 120


def digits_only(value):
    return ''.join(ch for ch in (value or '') if ch.isdigit())


def normalize_phone(value):
    """Brazilian phone -> '(11) 98765-4321' (10 or 11 digits, optional leading 55)."""
    value = (value or '').strip()
    if not value:
        return ''
    if any(not (ch.isdigit() or ch in ' ()-+.') for ch in value):
        raise ValidationError('Use apenas números, espaços, parênteses e hífen.')
    digits = digits_only(value)
    if len(digits) in (12, 13) and digits.startswith('55'):
        digits = digits[2:]
    if len(digits) not in (10, 11):
        raise ValidationError('Informe o telefone com DDD (10 ou 11 dígitos).')
    if len(digits) == 11:
        return f'({digits[:2]}) {digits[2:7]}-{digits[7:]}'
    return f'({digits[:2]}) {digits[2:6]}-{digits[6:]}'


def normalize_state(value):
    value = (value or '').strip().upper()
    if value and value not in BR_STATES:
        raise ValidationError('Informe a sigla de um estado válido (ex.: SP).')
    return value


def normalize_zip_code(value):
    value = (value or '').strip()
    if not value:
        return ''
    digits = digits_only(value)
    if len(digits) != 8:
        raise ValidationError('O CEP deve ter 8 dígitos.')
    return f'{digits[:5]}-{digits[5:]}'


def validate_birth_date(value):
    from django.utils import timezone
    if value is None:
        return
    today = timezone.localdate()
    if value > today:
        raise ValidationError('A data de nascimento não pode estar no futuro.')
    if value.year < today.year - MAX_AGE_YEARS:
        raise ValidationError('Confira a data de nascimento: o ano parece incorreto.')
