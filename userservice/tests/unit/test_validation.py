"""Validation helper tests."""

from datetime import date, timedelta

import pytest

from app.core.exceptions import ValidationError
from app.core.validation import (
    validate_address_type,
    validate_currency,
    validate_date_of_birth,
    validate_gender,
    validate_phone,
    validate_pincode,
)


def test_validate_phone_accepts_ten_digit():
    assert validate_phone("9876543210") == "9876543210"


def test_validate_phone_accepts_plus_91():
    assert validate_phone("+91 98765 43210") == "9876543210"


def test_validate_phone_rejects_invalid():
    with pytest.raises(ValidationError, match="Invalid Indian mobile"):
        validate_phone("1234567890")


def test_validate_pincode():
    assert validate_pincode("560001") == "560001"


def test_validate_pincode_rejects_zero_start():
    with pytest.raises(ValidationError):
        validate_pincode("056001")


def test_validate_gender():
    assert validate_gender("Male") == "male"


def test_validate_address_type():
    assert validate_address_type("HOME") == "home"


def test_validate_currency_inr_only():
    assert validate_currency("inr") == "INR"
    with pytest.raises(ValidationError):
        validate_currency("USD")


def test_validate_dob_minimum_age():
    young = date.today() - timedelta(days=365 * 10)
    with pytest.raises(ValidationError, match="13 years"):
        validate_date_of_birth(young)
