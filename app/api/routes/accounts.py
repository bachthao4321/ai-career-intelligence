import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.deps import (
    AccountServiceDep,
    CurrentUser,
    SettingsDep,
    auth_rate_limit,
    same_origin_write,
)
from app.core.config import Settings
from app.db.models import User
from app.schemas.accounts import Login, Registration, SavedJobList, StatusUpdate, UserOut
from app.services.accounts import (
    EmailTaken,
    InvalidCredentials,
    JobNotFound,
    NotSaved,
    SavedLimitReached,
    UsernameTaken,
    session_cookie,
)

auth = APIRouter(prefix="/auth", tags=["Accounts"])
saved = APIRouter(prefix="/me/saved-jobs", tags=["Saved jobs"])
routers = [auth, saved]

guarded = [Depends(same_origin_write)]
login_guards = [Depends(same_origin_write), Depends(auth_rate_limit)]


def set_session(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        session_cookie(settings),
        token,
        max_age=settings.user_session_days * 86400,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        path="/",
    )


def signed_out(settings: Settings) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(
        session_cookie(settings), path="/", secure=settings.app_env == "production", httponly=True
    )
    return response


@auth.post("/register", status_code=201, response_model=UserOut, dependencies=login_guards)
async def register(
    body: Registration, response: Response, accounts: AccountServiceDep, settings: SettingsDep
) -> UserOut:
    try:
        user, token = await accounts.register(body)
    except EmailTaken as exc:
        raise HTTPException(409, "Email already registered") from exc
    except UsernameTaken as exc:
        raise HTTPException(409, "Username already taken") from exc
    set_session(response, settings, token)
    return user


@auth.post("/login", response_model=UserOut, dependencies=login_guards)
async def login(
    body: Login, response: Response, accounts: AccountServiceDep, settings: SettingsDep
) -> UserOut:
    try:
        user, token = await accounts.login(body)
    except InvalidCredentials as exc:
        raise HTTPException(401, "Invalid login or password") from exc
    set_session(response, settings, token)
    return user


@auth.post("/logout", status_code=204, dependencies=guarded)
async def logout(request: Request, accounts: AccountServiceDep, settings: SettingsDep) -> Response:
    await accounts.logout(request.cookies.get(session_cookie(settings), ""))
    return signed_out(settings)


@auth.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


@auth.delete("/me", status_code=204, dependencies=guarded)
async def delete_account(
    user: CurrentUser, accounts: AccountServiceDep, settings: SettingsDep
) -> Response:
    await accounts.delete(user)
    return signed_out(settings)


@saved.get("", response_model=SavedJobList)
async def list_saved(user: CurrentUser, accounts: AccountServiceDep) -> SavedJobList:
    return await accounts.saved_jobs(user)


@saved.put("/{job_id}", status_code=204, dependencies=guarded)
async def save_job(job_id: uuid.UUID, user: CurrentUser, accounts: AccountServiceDep) -> Response:
    try:
        await accounts.save_job(user, job_id)
    except JobNotFound as exc:
        raise HTTPException(404, "Job not found") from exc
    except SavedLimitReached as exc:
        raise HTTPException(409, "Saved job limit reached") from exc
    return Response(status_code=204)


@saved.delete("/{job_id}", status_code=204, dependencies=guarded)
async def unsave_job(job_id: uuid.UUID, user: CurrentUser, accounts: AccountServiceDep) -> Response:
    await accounts.unsave_job(user, job_id)
    return Response(status_code=204)


@saved.patch("/{job_id}", status_code=204, dependencies=guarded)
async def set_status(
    job_id: uuid.UUID, body: StatusUpdate, user: CurrentUser, accounts: AccountServiceDep
) -> Response:
    try:
        await accounts.set_status(user, job_id, body.status)
    except NotSaved as exc:
        raise HTTPException(404, "Job is not saved") from exc
    return Response(status_code=204)
