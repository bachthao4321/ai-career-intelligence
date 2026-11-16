from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ingestion import JobRecord


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[JobRecord] = Field(min_length=1, max_length=20)


class IndexInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_jobs: int = Field(default=5, ge=1, le=5)
