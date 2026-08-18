from types import SimpleNamespace

from app.notifications.otp_service import OtpService


def test_phone_destination_is_normalized_to_e164_for_lookup():
    settings = SimpleNamespace(
        phone_country_code="91",
        otp_length=6,
        otp_ttl_seconds=300,
        otp_resend_cooldown_seconds=60,
        otp_max_verify_attempts=5,
        rate_limit_otp_per_phone_hourly=5,
    )
    service = OtpService.__new__(OtpService)
    service._settings = settings

    assert service._normalize_destination("9876543210", destination_type="phone", country_code="91") == "+919876543210"
    assert service._normalize_destination("+919876543210", destination_type="phone", country_code="91") == "+919876543210"
    assert service._normalize_destination("919876543210", destination_type="phone", country_code="91") == "+919876543210"
