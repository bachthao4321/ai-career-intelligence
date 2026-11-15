from sqlalchemy import and_, true
from sqlalchemy.sql.elements import ColumnElement

from app.core.policies import LOCAL_ONLY_SOURCES, LOCAL_ONLY_URL_PATTERN
from app.db.models import Job


def visible_jobs(environment: str) -> ColumnElement[bool]:
    if environment == "local":
        return true()
    return and_(
        Job.source.not_in(LOCAL_ONLY_SOURCES),
        ~Job.source_url.op("~*")(LOCAL_ONLY_URL_PATTERN),
    )
