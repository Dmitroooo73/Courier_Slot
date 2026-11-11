import re
from datetime import UTC, date, datetime
from os import getenv
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from backend.pipeline.orders import MOSCOW, ZONES
from backend.pipeline.results import PostgresResults, StorageUnavailable
from backend.services.avatars import MAX_UPLOAD_BYTES, normalize_avatar, read_avatar, remove_avatar, upload_avatar
from backend.services.auth import Account, AuthStore, DirectoryEntry, DirectoryStats, Profile, Tokens


class AuthRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def validate_without_echo(request: Request):
            try:
                return await handle(request)
            except RequestValidationError as error:
                field = error.errors()[0]["loc"][-1]
                messages = {
                    "login": "Логин должен содержать от 3 до 32 допустимых символов.",
                    "password": "Проверьте длину пароля.",
                    "birthday": "Проверьте дату рождения.",
                    "display_name": "Имя должно содержать до 50 символов.",
                    "bio": "Описание должно содержать до 280 символов.",
                    "preferred_zone": "Выберите зону из списка.",
                    "new_password": "Новый пароль должен содержать от 12 до 128 символов.",
                }
                detail = messages.get(field, "Проверьте введённые данные.")
                raise HTTPException(422, detail, headers=NO_STORE) from None

        return validate_without_echo


auth_router = APIRouter(prefix="/api/auth", tags=["auth"], route_class=AuthRoute)
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])
bearer = HTTPBearer(auto_error=False)
REFRESH_COOKIE = "pult_refresh"
COOKIE_PATH = "/api/auth"
ACCESS_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
REFRESH_PATTERN = re.compile(r"[0-9a-f]{32}\.[A-Za-z0-9_-]{43}")
NO_STORE = {"Cache-Control": "no-store"}


class Credentials(BaseModel):
    login: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,31}$")]
    password: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("login", mode="before")
    @classmethod
    def normalize_login(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class Registration(Credentials):
    password: Annotated[str, Field(min_length=12, max_length=128)]


class PasswordChange(BaseModel):
    current_password: Annotated[str, Field(min_length=1, max_length=128)]
    new_password: Annotated[str, Field(min_length=12, max_length=128)]


class ProfileEdit(BaseModel):
    display_name: Annotated[str, Field(max_length=50)]
    bio: Annotated[str, Field(max_length=280)]
    birthday: date | None
    preferred_zone: Annotated[str, Field(max_length=24)] | None

    @field_validator("display_name", "bio")
    @classmethod
    def clean_text(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("invalid text")
        return value.strip()

    @field_validator("birthday")
    @classmethod
    def valid_birthday(cls, value: date | None) -> date | None:
        if value and not date(1900, 1, 1) <= value <= datetime.now(MOSCOW).date():
            raise ValueError("invalid date")
        return value

    @field_validator("preferred_zone")
    @classmethod
    def valid_zone(cls, value: str | None) -> str | None:
        if value is not None and all(zone.id != value for zone in ZONES):
            raise ValueError("unknown zone")
        return value


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: Account


class UserPage(BaseModel):
    users: list[DirectoryEntry]
    next_after: int | None
    stats: DirectoryStats


class Moderation(BaseModel):
    action: Literal["block", "unblock", "revoke_sessions"]


def get_store() -> AuthStore:
    connection = getenv("PULT_DATABASE_URL")
    if not connection:
        raise StorageUnavailable("Для аккаунтов подключите PostgreSQL через Docker Compose или PULT_DATABASE_URL.")
    return AuthStore(PostgresResults(connection))


Store = Annotated[AuthStore, Depends(get_store)]


def require_browser_header(x_pult_auth: Annotated[str | None, Header()] = None) -> None:
    if x_pult_auth != "1":
        raise HTTPException(403, "Запрос авторизации должен содержать X-Pult-Auth.", headers=NO_STORE)


same_origin = [Depends(require_browser_header)]


def unauthorized(message: str = "Войдите в аккаунт.") -> HTTPException:
    return HTTPException(401, message, headers={**NO_STORE, "WWW-Authenticate": "Bearer"})


def refresh_cookie_name(request: Request) -> str:
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    # Cookies share a host across ports. Keep the original name on the default
    # port so the installed dashboard can continue using existing sessions.
    return REFRESH_COOKIE if port == 8765 else f"{REFRESH_COOKIE}_{port}"


def refresh_cookie(request: Request) -> str | None:
    token = request.cookies.get(refresh_cookie_name(request), "")
    return token if REFRESH_PATTERN.fullmatch(token) else None


def cookie_secure(request: Request) -> bool:
    return request.url.scheme == "https" or getenv("PULT_AUTH_SECURE_COOKIE") == "1"


def clear_cookie(response: Response, request: Request) -> None:
    response.headers.update(NO_STORE)
    response.delete_cookie(refresh_cookie_name(request), path=COOKIE_PATH, secure=cookie_secure(request), httponly=True, samesite="strict")


def authenticated(response: Response, request: Request, account: Account, tokens: Tokens) -> AuthResponse:
    response.headers.update(NO_STORE)
    response.set_cookie(
        refresh_cookie_name(request), tokens.refresh, max_age=max(0, int((tokens.expires_at - datetime.now(UTC)).total_seconds())),
        path=COOKIE_PATH, secure=cookie_secure(request), httponly=True, samesite="strict",
    )
    return AuthResponse(access_token=tokens.access, user=account)


def limit_attempts(store: AuthStore, request: Request, action: str, limit: int) -> None:
    if not store.allow_attempt(request.client.host if request.client else "unknown", action, limit):
        raise HTTPException(429, "Слишком много попыток. Попробуйте через минуту.", headers={**NO_STORE, "Retry-After": "60"})


def current_account(store: Store, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Account:
    if credentials and ACCESS_PATTERN.fullmatch(credentials.credentials):
        account = store.account_for(credentials.credentials)
        if account:
            return account
    raise unauthorized()


CurrentAccount = Annotated[Account, Depends(current_account)]


def require_admin(account: CurrentAccount) -> None:
    if account.role != "admin":
        raise HTTPException(403, "Нужна роль администратора.", headers=NO_STORE)


@auth_router.post("/register", status_code=201, dependencies=same_origin)
def register(data: Registration, request: Request, response: Response, store: Store) -> AuthResponse:
    limit_attempts(store, request, "register", 6)
    session = store.register(data.login, data.password, refresh_cookie(request))
    if session is None:
        raise HTTPException(409, "Этот логин уже занят.", headers=NO_STORE)
    return authenticated(response, request, *session)


@auth_router.post("/login", dependencies=same_origin)
def login(data: Credentials, request: Request, response: Response, store: Store) -> AuthResponse:
    limit_attempts(store, request, "login", 12)
    session = store.login(data.login, data.password, refresh_cookie(request))
    if session is None:
        raise unauthorized("Неверный логин или пароль.")
    return authenticated(response, request, *session)


@auth_router.post("/refresh", dependencies=same_origin)
def refresh(request: Request, response: Response) -> AuthResponse:
    token = refresh_cookie(request)
    session = get_store().refresh_session(token) if token else None
    if session is None:
        raise unauthorized("Сессия закончилась. Войдите снова.")
    return authenticated(response, request, *session)


@auth_router.post("/logout", status_code=204, dependencies=same_origin)
def logout(request: Request, response: Response) -> None:
    token = refresh_cookie(request)
    if token:
        get_store().logout(token)
    clear_cookie(response, request)


@auth_router.get("/me")
def me(response: Response, account: CurrentAccount) -> Account:
    response.headers.update(NO_STORE)
    return account


def show_profile(profile: Profile | None, response: Response) -> Profile:
    if profile is None:
        raise HTTPException(404, "Профиль не найден.", headers=NO_STORE)
    response.headers.update(NO_STORE)
    return profile


@auth_router.get("/profile")
def get_profile(response: Response, account: CurrentAccount, store: Store) -> Profile:
    return show_profile(store.profile(account.id), response)


@auth_router.put("/profile", dependencies=same_origin)
def edit_profile(data: ProfileEdit, response: Response, account: CurrentAccount, store: Store) -> Profile:
    return show_profile(store.update_profile(account.id, data.display_name, data.bio, data.birthday, data.preferred_zone), response)


@auth_router.post("/change-password", status_code=204, dependencies=same_origin)
def change_password(data: PasswordChange, request: Request, response: Response, account: CurrentAccount, store: Store) -> None:
    limit_attempts(store, request, "change-password", 6)
    if data.current_password == data.new_password:
        raise HTTPException(422, "Введите новый пароль, отличающийся от текущего.", headers=NO_STORE)
    if not store.change_password(account.id, data.current_password, data.new_password):
        raise HTTPException(400, "Текущий пароль указан неверно.", headers=NO_STORE)
    clear_cookie(response, request)


@auth_router.put("/avatar", dependencies=same_origin)
async def put_avatar(request: Request, response: Response, account: CurrentAccount, store: Store) -> Profile:
    await run_in_threadpool(limit_attempts, store, request, "avatar", 12)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Выберите изображение до 4 МБ.", headers=NO_STORE)
        data.extend(chunk)
    try:
        normalized = await run_in_threadpool(normalize_avatar, bytes(data))
    except ValueError as error:
        raise HTTPException(422, str(error), headers=NO_STORE) from error
    return await run_in_threadpool(save_avatar, normalized, account.id, store, response)


def save_avatar(data: bytes, user_id: int, store: AuthStore, response: Response) -> Profile:
    avatar_id = upload_avatar(data)
    result = store.replace_avatar(user_id, avatar_id)
    if result is None:
        remove_avatar(avatar_id)
        raise unauthorized()
    profile, previous = result
    remove_avatar(previous)
    return show_profile(profile, response)


@auth_router.delete("/avatar", dependencies=same_origin)
def delete_avatar(response: Response, account: CurrentAccount, store: Store) -> Profile:
    result = store.replace_avatar(account.id, None)
    if result is None:
        raise unauthorized()
    profile, previous = result
    remove_avatar(previous)
    return show_profile(profile, response)


@auth_router.get("/avatars/{user_id}", response_class=Response)
def avatar(user_id: int, account: CurrentAccount, store: Store) -> Response:
    if account.id != user_id and account.role != "admin":
        raise HTTPException(403, "Доступ к аватару ограничен.", headers=NO_STORE)
    profile = store.profile(user_id)
    data = read_avatar(profile.avatar_id) if profile and profile.avatar_id else None
    if data is None:
        raise HTTPException(404, "Аватар не найден.", headers=NO_STORE)
    return Response(data, media_type="image/webp", headers={**NO_STORE, "X-Content-Type-Options": "nosniff"})


@admin_router.get("/users", dependencies=[Depends(require_admin)])
def users(
    response: Response, store: Store, after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    q: Annotated[str, Query(max_length=50, pattern=r"^[^\x00]*$")] = "",
    role: Literal["", "user", "admin"] = "", status: Literal["", "active", "disabled"] = "",
) -> UserPage:
    response.headers.update(NO_STORE)
    page_size = 50
    accounts, stats = store.users(after, page_size + 1, q.strip(), role, status)
    page = accounts[:page_size]
    return UserPage(users=page, next_after=page[-1].id if len(accounts) > page_size else None, stats=stats)


@admin_router.get("/users/{user_id}", dependencies=[Depends(require_admin)])
def admin_profile(user_id: int, response: Response, store: Store) -> Profile:
    return show_profile(store.profile(user_id), response)


@admin_router.post("/users/{user_id}/actions", dependencies=[Depends(require_admin), *same_origin])
def moderate_user(user_id: int, data: Moderation, response: Response, store: Store) -> Profile:
    updated = store.moderate(user_id, data.action)
    if updated is None:
        raise HTTPException(404, "Действие доступно для обычных аккаунтов.", headers=NO_STORE)
    return show_profile(updated, response)
