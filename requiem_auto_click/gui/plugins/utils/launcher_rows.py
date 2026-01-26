from __future__ import annotations

import json
from dataclasses import dataclass

# pylint: disable=broad-exception-caught

from ...constants import LAUNCHER_ROWS_JSON_GLOBAL_KEY


@dataclass(frozen=True)
class LauncherRow:
    login: str
    nickname: str
    pid: int


def parse_launcher_rows_json(raw: str) -> list[LauncherRow]:
    raw = str(raw or "")
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[LauncherRow] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        login = str(item.get("login", "") or "").strip()
        nickname = str(item.get("nickname", "") or "").strip()
        try:
            pid = int(item.get("pid", 0) or 0)
        except Exception:
            pid = 0
        out.append(LauncherRow(login=login, nickname=nickname, pid=pid))
    return out


def load_launcher_rows_raw_anywhere(tab_context) -> str:
    """
    Возвращает JSON строку rows из Launcher.

    Приоритет:
    1) global key (пишется LauncherPlugin'ом в общий scope)
    2) fallback: ищем сохранённые rows_json по всем вкладкам (tabs/*/launcher/rows_json),
       чтобы подхватить старые настройки, созданные до появления global key.
    """
    ctx = tab_context
    if ctx is None:
        return ""

    # 1) global
    try:
        raw = str(ctx.get_global_value(LAUNCHER_ROWS_JSON_GLOBAL_KEY, "", value_type=str) or "")
    except Exception:
        raw = ""
    if str(raw).strip():
        return str(raw)

    # 2) scan allKeys for old per-tab stored values
    try:
        settings = getattr(ctx, "settings", None)
        if settings is None:
            return ""
        keys = list(settings.allKeys() or [])
    except Exception:
        keys = []
        settings = None

    best = ""
    for k in keys:
        ks = str(k or "")
        if not ks:
            continue
        if not ks.startswith("tabs/"):
            continue
        if not ks.endswith("/launcher/rows_json"):
            continue
        try:
            v = str(settings.value(ks, "", type=str) or "")
        except Exception:
            v = ""
        if v.strip():
            best = v
            break
    return str(best or "")

