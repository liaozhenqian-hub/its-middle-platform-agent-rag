from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from knowledge.auth.identity import AuthenticationError
from knowledge.auth.service import (
    InvalidOAuthStateError,
    UserAuthService,
    UserAuthUnavailableError,
    UserCsrfError,
)


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", tags=["user-auth"])

    @router.get("/feishu/start")
    async def feishu_start(request: Request, redirect: str = "/chat"):
        service = _service(request)
        try:
            result = await service.begin_login(
                request.cookies.get(service.settings.anonymous_cookie_name),
                redirect_path=redirect,
            )
        except UserAuthUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        response = RedirectResponse(result.authorization_url, status_code=307)
        _set_anonymous_cookie(
            response,
            service,
            result.identity_resolution.cookie_value,
            samesite="lax",
        )
        return response

    @router.get("/feishu/callback")
    async def feishu_callback(request: Request, code: str, state: str):
        service = _service(request)
        try:
            result = await service.complete_login(
                code=code,
                state=state,
                anonymous_cookie=request.cookies.get(
                    service.settings.anonymous_cookie_name
                ),
            )
        except InvalidOAuthStateError as exc:
            raise HTTPException(status_code=400, detail="OAuth state is invalid") from exc
        except UserAuthUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        response = RedirectResponse(result.redirect_path, status_code=303)
        response.set_cookie(
            service.settings.user_session_cookie_name,
            result.session_secret,
            max_age=service.settings.user_session_sliding_ttl_seconds,
            httponly=True,
            secure=service.settings.user_cookie_secure,
            samesite="strict",
            path="/",
        )
        return response

    @router.get("/me")
    async def me(request: Request):
        service = _service(request)
        try:
            resolution = await _resolve_request(service, request)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail="invalid bearer credentials") from exc
        identity = resolution.identity
        payload = {
            "owner_id": identity.owner_id,
            "identity_kind": identity.kind,
            "authenticated": identity.kind in {"feishu", "personal_token"},
            "display_name": identity.display_name,
            "csrf_token": identity.csrf_token if identity.kind == "feishu" else None,
            "scopes": sorted(identity.scopes),
            "merge_available": False,
            "feishu_login_available": service.settings.feishu_oauth_available,
            "feishu_login_url": (
                f"{service.settings.user_public_base_url}"
                "/api/v1/auth/feishu/start"
            ),
        }
        if identity.kind == "feishu" and identity.session_id:
            session = await service.repository.get_user_session(identity.session_id)
            payload["merge_available"] = bool(session.source_anonymous_owner_id)
        response = JSONResponse(payload)
        _apply_resolution_cookies(response, service, resolution, request)
        return response

    @router.post("/logout", status_code=204)
    async def logout(request: Request):
        service = _service(request)
        try:
            resolution = await _resolve_request(service, request)
            await service.logout(
                resolution.identity, request.headers.get("X-User-CSRF-Token")
            )
        except UserCsrfError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail="invalid bearer credentials") from exc
        response = Response(status_code=204)
        response.delete_cookie(
            service.settings.user_session_cookie_name,
            path="/",
            secure=service.settings.user_cookie_secure,
            httponly=True,
            samesite="strict",
        )
        response.delete_cookie(
            service.settings.anonymous_cookie_name,
            path="/",
            secure=service.settings.user_cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.get("/merge-preview")
    async def merge_preview(request: Request):
        service = _service(request)
        try:
            resolution = await _resolve_request(service, request)
            payload = await service.merge_preview(resolution.identity)
        except UserCsrfError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except UserAuthUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        response = JSONResponse(payload)
        _apply_resolution_cookies(response, service, resolution, request)
        return response

    @router.post("/merge-anonymous")
    async def merge_anonymous(body: _MergeRequest, request: Request):
        service = _service(request)
        try:
            resolution = await _resolve_request(service, request)
            payload = await service.merge_anonymous(
                resolution.identity,
                csrf_token=request.headers.get("X-User-CSRF-Token"),
                confirm=body.confirm,
            )
        except UserCsrfError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except UserAuthUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        response = JSONResponse(payload)
        if body.confirm and payload.get("status") == "completed":
            response.delete_cookie(
                service.settings.anonymous_cookie_name,
                path="/",
                secure=service.settings.user_cookie_secure,
                httponly=True,
                samesite="strict",
            )
        return response

    return router


class _MergeRequest(BaseModel):
    confirm: bool


class _TokenCreateRequest(BaseModel):
    name: str
    scopes: list[str]


def create_account_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/account", tags=["user-account"])

    @router.get("/tokens")
    async def list_tokens(request: Request):
        service = _service(request)
        identity = await _require_feishu_identity(service, request, write=False)
        return jsonable_encoder(
            await service.personal_tokens.list(identity.owner_id)
        )

    @router.post("/tokens", status_code=201)
    async def create_token(body: _TokenCreateRequest, request: Request):
        service = _service(request)
        identity = await _require_feishu_identity(service, request, write=True)
        try:
            created = await service.personal_tokens.create(
                identity.owner_id, name=body.name, scopes=body.scopes
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(
            status_code=201,
            content={
                "token": created.plaintext,
                "item": jsonable_encoder(created.token),
            },
        )

    @router.delete("/tokens/{token_id}", status_code=204)
    async def revoke_token(token_id: str, request: Request):
        service = _service(request)
        identity = await _require_feishu_identity(service, request, write=True)
        revoked = await service.personal_tokens.revoke(
            identity.owner_id, token_id
        )
        if not revoked:
            raise HTTPException(status_code=404, detail="personal token not found")
        return Response(status_code=204)

    return router


async def _require_feishu_identity(
    service: UserAuthService, request: Request, *, write: bool
):
    try:
        resolution = await _resolve_request(service, request)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="invalid bearer credentials") from exc
    if resolution.identity.kind != "feishu":
        raise HTTPException(
            status_code=403, detail="Feishu user session is required"
        )
    if write:
        try:
            service.validate_user_csrf(
                resolution.identity,
                request.headers.get("X-User-CSRF-Token"),
            )
        except UserCsrfError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return resolution.identity


def _service(request: Request) -> UserAuthService:
    service = getattr(request.app.state, "user_auth_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="user authentication unavailable")
    return service


async def _resolve_request(service: UserAuthService, request: Request):
    return await service.resolve(
        authorization=request.headers.get("Authorization"),
        user_session_cookie=request.cookies.get(
            service.settings.user_session_cookie_name
        ),
        anonymous_cookie=request.cookies.get(service.settings.anonymous_cookie_name),
    )


def _apply_resolution_cookies(response, service, resolution, request) -> None:
    _set_anonymous_cookie(response, service, resolution.cookie_value)
    if resolution.clear_user_cookie:
        response.delete_cookie(service.settings.user_session_cookie_name, path="/")
    elif resolution.identity.kind == "feishu":
        existing = request.cookies.get(service.settings.user_session_cookie_name)
        if existing:
            response.set_cookie(
                service.settings.user_session_cookie_name,
                existing,
                max_age=service.settings.user_session_sliding_ttl_seconds,
                httponly=True,
                secure=service.settings.user_cookie_secure,
                samesite="strict",
                path="/",
            )


def _set_anonymous_cookie(
    response,
    service,
    value: str | None,
    *,
    samesite: str = "strict",
) -> None:
    if not value:
        return
    response.set_cookie(
        service.settings.anonymous_cookie_name,
        value,
        max_age=service.settings.anonymous_device_ttl_seconds,
        httponly=True,
        secure=service.settings.user_cookie_secure,
        samesite=samesite,
        path="/",
    )
