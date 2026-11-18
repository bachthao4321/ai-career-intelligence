import pytest
from pydantic import ValidationError

from app.core.security.passwords import hash_password, verify_password
from app.schemas.accounts import Login, Registration, StatusUpdate


def test_password_hash_round_trip_uses_unique_salts() -> None:
    first, second = hash_password("correct horse"), hash_password("correct horse")
    assert first != second and first.startswith("scrypt$131072$8$1$")
    assert verify_password("correct horse", first)
    assert not verify_password("correct horsf", first)
    assert not verify_password("correct horse", "bcrypt$whatever")
    assert not verify_password("correct horse", "scrypt$broken")


def registration(**update: str) -> Registration:
    return Registration.model_validate(
        {"email": "ana@example.com", "username": "ana", "password": "12345678", **update}
    )


def test_registration_normalizes_email_and_username() -> None:
    account = registration(email="  Ana@Example.COM ", username=" Ana.Nguyen_2 ")
    assert (account.email, account.username) == ("ana@example.com", "ana.nguyen_2")
    for email in ("ana", "ana@example", "a b@example.com", "@example.com", "ana@@example.com"):
        with pytest.raises(ValidationError):
            registration(email=email)
    for password in ("1234567", "x" * 129):
        with pytest.raises(ValidationError):
            registration(password=password)
    with pytest.raises(ValidationError):
        Registration.model_validate(
            {"email": "a@b.co", "username": "ana", "password": "12345678", "x": 1}
        )


def test_username_rule_matches_database_check() -> None:
    for good in ("ana", "a_b", "ana.nguyen", "x" * 30, "007"):
        assert registration(username=good).username == good
    for bad in ("ab", "x" * 31, "_ana", "ana.", "an a", "ana@x", "an-a", "ánh"):
        with pytest.raises(ValidationError):
            registration(username=bad)


def test_login_accepts_email_or_username() -> None:
    assert Login(login=" Ana@Example.com ", password="12345678").login == "ana@example.com"
    assert Login(login="Ana.Nguyen", password="12345678").login == "ana.nguyen"
    with pytest.raises(ValidationError):
        Login(login="ab", password="12345678")


def test_status_update_accepts_only_pipeline_states() -> None:
    for status in ("saved", "applied", "interviewing", "offer", "rejected"):
        assert StatusUpdate(status=status).status == status
    for bad in ("hired", "", "APPLIED"):
        with pytest.raises(ValidationError):
            StatusUpdate.model_validate({"status": bad})
