import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import get_services
from app.schemas.cv import MAX_PDF_BYTES, CVError, CVUploaded, CVView, MatchReport, MatchRequest
from app.services.cv.service import CVCapacityReached, CVNotFound, CVService
from app.services.search.documents import CorpusTooLarge

router = APIRouter(prefix="/cv", tags=["CV matching"])
routers = [router]
bearer = HTTPBearer(auto_error=False)


def access_token(auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    if auth is None or not 32 <= len(auth.credentials) <= 128:
        raise HTTPException(404, "CV not found or expired")
    return auth.credentials


def service(request: Request) -> CVService:
    return get_services(request).cv


@router.post(
    "",
    status_code=201,
    response_model=CVUploaded,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def upload_cv(request: Request) -> CVUploaded:
    cv = service(request)
    if request.headers.get("content-type", "").split(";")[0].lower() != "application/pdf":
        raise HTTPException(415, "Send a raw application/pdf request body")
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isdecimal():
            raise HTTPException(400, "Invalid content length")
        if len(length) > 10 or int(length) > MAX_PDF_BYTES:
            raise HTTPException(413, "PDF exceeds 2 MiB")
    try:
        await asyncio.wait_for(cv.upload_slots.acquire(), timeout=0.1)
    except TimeoutError as exc:
        raise HTTPException(503, "CV parser busy; retry later") from exc
    try:
        data = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_PDF_BYTES:
                        raise HTTPException(413, "PDF exceeds 2 MiB")
                    data.extend(chunk)
        except TimeoutError as exc:
            raise HTTPException(408, "Upload timed out") from exc
        return await cv.upload(bytes(data))
    except CVError as exc:
        raise HTTPException(422, str(exc)) from exc
    except CVCapacityReached as exc:
        raise HTTPException(503, "CV storage capacity reached; retry later") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "CV storage unavailable") from exc
    finally:
        cv.upload_slots.release()


@router.get("/{cv_id}", response_model=CVView)
async def get_cv(
    cv_id: uuid.UUID, request: Request, token: Annotated[str, Depends(access_token)]
) -> CVView:
    try:
        return await service(request).get(cv_id, token)
    except CVNotFound as exc:
        raise HTTPException(404, "CV not found or expired") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "CV storage unavailable") from exc


@router.delete("/{cv_id}", status_code=204)
async def delete_cv(
    cv_id: uuid.UUID, request: Request, token: Annotated[str, Depends(access_token)]
) -> Response:
    try:
        await service(request).delete(cv_id, token)
        return Response(status_code=204)
    except CVNotFound as exc:
        raise HTTPException(404, "CV not found or expired") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "CV storage unavailable") from exc


@router.post("/{cv_id}/matches", response_model=MatchReport)
async def match_cv(
    cv_id: uuid.UUID,
    body: MatchRequest,
    request: Request,
    token: Annotated[str, Depends(access_token)],
) -> MatchReport:
    try:
        return await service(request).match(cv_id, token, body)
    except CVNotFound as exc:
        raise HTTPException(404, "CV not found or expired") from exc
    except CorpusTooLarge as exc:
        raise HTTPException(422, "Corpus limit exceeded; narrow filters") from exc
    except CVCapacityReached as exc:
        raise HTTPException(503, "CV matching busy; retry later") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, "CV or jobs storage unavailable") from exc
