from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import JobServiceDep
from app.schemas.jobs import BrowsePage, BrowseParams
from app.schemas.platform import Overview

router = APIRouter(prefix="/platform", tags=["Platform"])
routers = [router]


@router.get("/overview", response_model=Overview)
async def overview(jobs: JobServiceDep) -> Overview:
    return await jobs.overview()


@router.get("/jobs", response_model=BrowsePage)
async def browse_jobs(jobs: JobServiceDep, params: Annotated[BrowseParams, Query()]) -> BrowsePage:
    return await jobs.browse(params)
