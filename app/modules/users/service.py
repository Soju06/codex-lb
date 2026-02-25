from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import bcrypt
from sqlalchemy.exc import IntegrityError

from app.core.config.settings import get_settings
from app.db.models import DashboardUser, DashboardUserRole
from app.modules.users.repository import _UNSET, UsersRepository

DEFAULT_ADMIN_USERNAME = "admin"
BOOTSTRAP_ADMIN_PASSWORD_ENV = "CODEX_LB_BOOTSTRAP_ADMIN_PASSWORD"


class DashboardUserNotFoundError(ValueError):
    pass


class DashboardUserConflictError(ValueError):
    pass


class DashboardUserOperationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DashboardUserData:
    id: str
    username: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DashboardUserCreateData:
    username: str
    password: str
    role: str


@dataclass(frozen=True, slots=True)
class DashboardUserUpdateData:
    username: str | None = None
    username_set: bool = False
    password: str | None = None
    password_set: bool = False
    role: str | None = None
    role_set: bool = False
    is_active: bool | None = None
    is_active_set: bool = False


class UsersService:
    def __init__(self, repository: UsersRepository) -> None:
        self._repository = repository

    async def ensure_default_admin(self) -> DashboardUserData:
        existing = await self._repository.get_by_username(DEFAULT_ADMIN_USERNAME)
        if existing is not None:
            return _to_data(existing)

        bootstrap_password = get_settings().bootstrap_admin_password
        if bootstrap_password is None:
            raise DashboardUserOperationError(
                f"{BOOTSTRAP_ADMIN_PASSWORD_ENV} must be configured before initial admin bootstrap",
            )

        user = DashboardUser(
            id=str(uuid.uuid4()),
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=_hash_password(bootstrap_password),
            role=DashboardUserRole.ADMIN,
            is_active=True,
        )
        try:
            created = await self._repository.create(user)
        except IntegrityError:
            # Concurrent startup path.
            existing = await self._repository.get_by_username(DEFAULT_ADMIN_USERNAME)
            if existing is None:
                raise
            return _to_data(existing)
        return _to_data(created)

    async def list_users(self, *, search: str | None = None, role: str | None = None) -> list[DashboardUserData]:
        rows = await self._repository.list_users(search=search, role=role)
        return [_to_data(row) for row in rows]

    async def create_user(self, payload: DashboardUserCreateData) -> DashboardUserData:
        normalized_role = _normalize_role(payload.role)
        username = _normalize_username(payload.username)
        if len(payload.password.strip()) < 8:
            raise DashboardUserOperationError("Password must be at least 8 characters")
        user = DashboardUser(
            id=str(uuid.uuid4()),
            username=username,
            password_hash=_hash_password(payload.password),
            role=DashboardUserRole(normalized_role),
            is_active=True,
        )
        try:
            created = await self._repository.create(user)
        except IntegrityError as exc:
            raise DashboardUserConflictError(f"User with username '{username}' already exists") from exc
        return _to_data(created)

    async def update_user(
        self,
        user_id: str,
        payload: DashboardUserUpdateData,
        *,
        actor_user_id: str,
    ) -> DashboardUserData:
        existing = await self._repository.get_by_id(user_id)
        if existing is None:
            raise DashboardUserNotFoundError(f"User not found: {user_id}")

        username = _normalize_username(payload.username or "") if payload.username_set else _UNSET
        if payload.password_set:
            assert payload.password is not None
            if len(payload.password.strip()) < 8:
                raise DashboardUserOperationError("Password must be at least 8 characters")
            password_hash = _hash_password(payload.password)
        else:
            password_hash = _UNSET
        role = _normalize_role(payload.role or "") if payload.role_set else _UNSET
        is_active = payload.is_active if payload.is_active_set and payload.is_active is not None else _UNSET

        if existing.id == actor_user_id:
            existing_role = existing.role.value if isinstance(existing.role, DashboardUserRole) else str(existing.role)
            if role is not _UNSET and str(role) != "admin" and existing_role == "admin":
                admin_count = await self._repository.count_admins()
                if admin_count <= 1:
                    raise DashboardUserOperationError("Cannot remove the last active admin role")
            if is_active is not _UNSET and is_active is False and existing_role == "admin":
                admin_count = await self._repository.count_admins()
                if admin_count <= 1:
                    raise DashboardUserOperationError("Cannot deactivate the last active admin")

        try:
            updated = await self._repository.update(
                user_id,
                username=username,
                password_hash=password_hash,
                role=role,
                is_active=is_active,
            )
        except IntegrityError as exc:
            raise DashboardUserConflictError("Username already exists") from exc
        if updated is None:
            raise DashboardUserNotFoundError(f"User not found: {user_id}")
        return _to_data(updated)

    async def delete_user(self, user_id: str, *, actor_user_id: str) -> None:
        existing = await self._repository.get_by_id(user_id)
        if existing is None:
            raise DashboardUserNotFoundError(f"User not found: {user_id}")
        if existing.id == actor_user_id:
            raise DashboardUserOperationError("Cannot delete the current user")
        if existing.role == DashboardUserRole.ADMIN and existing.is_active:
            admin_count = await self._repository.count_admins()
            if admin_count <= 1:
                raise DashboardUserOperationError("Cannot delete the last active admin")
        deleted = await self._repository.delete(user_id)
        if not deleted:
            raise DashboardUserNotFoundError(f"User not found: {user_id}")

    async def verify_credentials(self, username: str, password: str) -> DashboardUserData | None:
        row = await self._repository.get_by_username(_normalize_username(username))
        if row is None or not row.is_active:
            return None
        if not _check_password(password, row.password_hash):
            return None
        return _to_data(row)

    async def get_user(self, user_id: str) -> DashboardUserData | None:
        row = await self._repository.get_by_id(user_id)
        if row is None:
            return None
        return _to_data(row)


def _to_data(row: DashboardUser) -> DashboardUserData:
    return DashboardUserData(
        id=row.id,
        username=row.username,
        role=row.role.value if isinstance(row.role, DashboardUserRole) else str(row.role),
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def _normalize_username(username: str) -> str:
    value = username.strip().lower()
    if not value:
        raise DashboardUserOperationError("Username is required")
    return value


def _normalize_role(role: str) -> str:
    value = role.strip().lower()
    if value not in {"admin", "user"}:
        raise DashboardUserOperationError("Role must be 'admin' or 'user'")
    return value
