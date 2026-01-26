from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoginRowState:
    row_id: str
    login: str
    nickname: str
    pid: int

    @property
    def is_active(self) -> bool:
        return int(self.pid or 0) > 0


def unique_logins_in_order(rows: list[LoginRowState]) -> list[str]:
    seen = set()
    out: list[str] = []
    for r in rows:
        login = str(r.login or "").strip()
        if not login:
            continue
        if login in seen:
            continue
        seen.add(login)
        out.append(login)
    return out


def unique_nicknames_in_order(rows: list[LoginRowState]) -> list[str]:
    seen = set()
    out: list[str] = []
    for r in rows:
        nick = str(r.nickname or "").strip()
        if not nick:
            continue
        if nick in seen:
            continue
        seen.add(nick)
        out.append(nick)
    return out


def active_logins(rows: list[LoginRowState]) -> set[str]:
    out: set[str] = set()
    for r in rows:
        if r.is_active and str(r.login or "").strip():
            out.add(str(r.login).strip())
    return out


def active_nicknames(rows: list[LoginRowState]) -> set[str]:
    out: set[str] = set()
    for r in rows:
        if r.is_active and str(r.nickname or "").strip():
            out.add(str(r.nickname).strip())
    return out


def active_pids(rows: list[LoginRowState]) -> set[int]:
    out: set[int] = set()
    for r in rows:
        if r.is_active:
            out.add(int(r.pid))
    out.discard(0)
    return out


def first_inactive_row_for_login(rows: list[LoginRowState], login: str) -> str | None:
    login = str(login or "").strip()
    if not login:
        return None
    for r in rows:
        if str(r.login or "").strip() == login and not r.is_active:
            return r.row_id
    return None


def first_inactive_row_for_nickname(rows: list[LoginRowState], nickname: str) -> str | None:
    nickname = str(nickname or "").strip()
    if not nickname:
        return None
    for r in rows:
        if str(r.nickname or "").strip() == nickname and not r.is_active:
            return r.row_id
    return None


def active_pid_for_login(rows: list[LoginRowState], login: str) -> int:
    """Активный PID для логина (если есть), иначе 0."""
    login = str(login or "").strip()
    if not login:
        return 0
    for r in rows:
        if r.is_active and str(r.login or "").strip() == login:
            return int(r.pid)
    return 0


def active_pid_for_nickname(rows: list[LoginRowState], nickname: str) -> int:
    """Активный PID для ника (если есть), иначе 0."""
    nickname = str(nickname or "").strip()
    if not nickname:
        return 0
    for r in rows:
        if r.is_active and str(r.nickname or "").strip() == nickname:
            return int(r.pid)
    return 0

