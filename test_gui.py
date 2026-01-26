"""Локальный запуск GUI из репозитория (для отладки с брейкпоинтами).

Пример:
  python test_gui.py
"""

from __future__ import annotations

from requiem_auto_click.gui.app import run_gui


if __name__ == "__main__":
    raise SystemExit(run_gui())

