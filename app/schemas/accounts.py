import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.jobs import JobSummary

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")
# 3-30 chars; letters, digits, dot and underscore; starts and ends alphanumeric (mirrors DB CHECK).
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._]{1,28}[a-z0-9]$")
MAX_SAVED_JOBS = 500
# Order is the application pipeline; the database CHECK constraint mirrors it.
ApplicationStatus = Literal["saved", "applied", "interviewing", "offer", "rejected"]


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL.fullmatch(value):
            raise ValueError("Invalid email address")
        return value

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not USERNAME.fullmatch(value):
            raise ValueError(
                "Username must be 3-30 letters, digits, dots or underscores, "
                "starting and ending with a letter or digit"
            )
        return value


class Login(BaseModel):
    """`login` is an email (contains @) or a username."""

    model_config = ConfigDict(extra="forbid")
    login: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("login")
    @classmethod
    def normalize_login(cls, value: str) -> str:
        return value.strip().lower()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    username: str
    created_at: datetime


class SavedJobOut(BaseModel):
    job: JobSummary
    saved_at: datetime
    status: ApplicationStatus
    status_updated_at: datetime


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: ApplicationStatus


class SavedJobList(BaseModel):
    items: list[SavedJobOut]
    limit: int = MAX_SAVED_JOBS
