import pytest
from pydantic import ValidationError

from app.core.security.passwords import hash_password, verify_password
from app.schemas.accounts import Credentials, StatusUpdate


def test_password_hash_round_trip_uses_unique_salts() -> None:
    first, second = hash_password("correct horse"), hash_password("correct horse")
    assert first != second and first.startswith("scrypt$131072$8$1$")
    assert verify_password("correct horse", first)
    assert not verify_password("correct horsf", first)
    assert not verify_password("correct horse", "bcrypt$whatever")
    assert not verify_password("correct horse", "scrypt$broken")


def test_credentials_normalize_email_and_bound_password() -> None:
    assert Credentials(email="  Ana@Example.COM ", password="12345678").email == "ana@example.com"
    for email in ("ana", "ana@example", "a b@example.com", "@example.com", "ana@@example.com"):
        with pytest.raises(ValidationError):
            Credentials(email=email, password="12345678")
    with pytest.raises(ValidationError):
        Credentials(email="ana@example.com", password="1234567")
    with pytest.raises(ValidationError):
        Credentials(email="ana@example.com", password="x" * 129)
    with pytest.raises(ValidationError):
        Credentials.model_validate({"email": "ana@example.com", "password": "12345678", "x": 1})


def test_status_update_accepts_only_pipeline_states() -> None:
    for status in ("saved", "applied", "interviewing", "offer", "rejected"):
        assert StatusUpdate(status=status).status == status
    for bad in ("hired", "", "APPLIED"):
        with pytest.raises(ValidationError):
            StatusUpdate.model_validate({"status": bad})
