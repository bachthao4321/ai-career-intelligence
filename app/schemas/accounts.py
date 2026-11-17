import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.jobs import JobSummary

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")
MAX_SAVED_JOBS = 500
# Order is the application pipeline; the database CHECK constraint mirrors it.
ApplicationStatus = Literal["saved", "applied", "interviewing", "offer", "rejected"]


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL.fullmatch(value):
            raise ValueError("Invalid email address")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
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
