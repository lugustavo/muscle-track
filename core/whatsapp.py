"""WhatsApp click-to-chat links (nothing is sent automatically)."""
from urllib.parse import quote


def phone_digits_br(phone):
    """Digits of a Brazilian phone with the country code, or '' when there is no phone."""
    digits = ''.join(ch for ch in (phone or '') if ch.isdigit())
    if digits and not digits.startswith('55') and len(digits) <= 11:
        digits = '55' + digits  # typed without the country code
    return digits


def whatsapp_url(phone, text):
    """wa.me link with the message ready; without a phone WhatsApp asks which contact to use."""
    return f'https://wa.me/{phone_digits_br(phone)}?text={quote(text)}'
