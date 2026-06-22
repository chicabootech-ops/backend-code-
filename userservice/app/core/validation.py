"""Input validation helpers for account domain."""

from __future__ import annotations

import re
from datetime import date

from app.core.exceptions import ValidationError

INDIAN_PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")
GENDERS = frozenset({"male", "female", "other", "prefer_not_to_say"})
ADDRESS_TYPES = frozenset({"shipping", "billing", "home", "office", "other"})
DEVICE_TYPES = frozenset({"mobile", "tablet", "desktop", "unknown"})
LANGUAGES = frozenset({"en", "hi"})
CURRENCIES = frozenset({"INR"})


def normalize_phone(phone: str) -> str:
    cleaned = re.sub(r"[\s\-()]", "", phone.strip())
    if cleaned.startswith("+91"):
        cleaned = cleaned[3:]
    elif cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
    return cleaned


def validate_phone(phone: str) -> str:
    normalized = normalize_phone(phone)
    if not re.match(r"^[6-9]\d{9}$", normalized):
        raise ValidationError("Invalid Indian mobile number", code="invalid_phone")
    return normalized


def validate_pincode(pincode: str) -> str:
    pin = pincode.strip()
    if not INDIAN_PINCODE_RE.match(pin):
        raise ValidationError("Invalid Indian postal code (6 digits)", code="invalid_pincode")
    return pin


def validate_gender(gender: str) -> str:
    value = gender.strip().lower()
    if value not in GENDERS:
        raise ValidationError(f"Gender must be one of: {', '.join(sorted(GENDERS))}", code="invalid_gender")
    return value


def validate_address_type(address_type: str) -> str:
    value = address_type.strip().lower()
    if value not in ADDRESS_TYPES:
        raise ValidationError(
            f"address_type must be one of: {', '.join(sorted(ADDRESS_TYPES))}",
            code="invalid_address_type",
        )
    return value


def validate_date_of_birth(dob: date) -> date:
    today = date.today()
    if dob > today:
        raise ValidationError("Date of birth cannot be in the future", code="invalid_dob")
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < 13:
        raise ValidationError("You must be at least 13 years old", code="invalid_dob")
    return dob


def validate_language(language: str) -> str:
    value = language.strip().lower()
    if value not in LANGUAGES:
        raise ValidationError(f"Language must be one of: {', '.join(sorted(LANGUAGES))}", code="invalid_language")
    return value


def validate_currency(currency: str) -> str:
    value = currency.strip().upper()
    if value not in CURRENCIES:
        raise ValidationError(f"Currency must be one of: {', '.join(sorted(CURRENCIES))}", code="invalid_currency")
    return value
