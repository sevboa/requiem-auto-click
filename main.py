"""Консольная точка входа для запуска автоматизации Requiem.

Запуск:
  python main.py sharpening_items_to --config configs/example_sharpening.py
  python main.py disassemble_items   --config configs/example_disassemble.py
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from modules.requiem_clicker import RequiemClicker
from modules.windows_mouse_client import WindowsMouseClient


def _load_config_module(config_path: str) -> ModuleType:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config не найден: {path}")
    if path.suffix.lower() != ".py":
        raise ValueError(f"Config должен быть .py файлом: {path}")

    module_name = f"requiem_config_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Не удалось загрузить config как модуль: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _get_opt(module: ModuleType, name: str, default: Any) -> Any:
    return getattr(module, name, default)


def _require(module: ModuleType, name: str) -> Any:
    if not hasattr(module, name):
        raise ValueError(f"В config отсутствует обязательная переменная `{name}`")
    return getattr(module, name)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="requiem-auto-click",
        description="CLI to run Requiem automation scenarios (sharpen/disassemble).",
    )
    p.add_argument(
        "method",
        choices=["sharpening_items_to", "disassemble_items"],
        help="Which scenario to run.",
    )
    p.add_argument(
        "--config",
        required=True,
        help="Path to a .py config file (targets/retries and optional settings).",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = _load_config_module(args.config)

    window_title_substring: str = str(_get_opt(cfg, "window_title_substring", "Requiem"))
    wait_for_backspace_on_init: bool = bool(_get_opt(cfg, "wait_for_backspace_on_init", True))
    confirm_with_bracket: bool = bool(_get_opt(cfg, "confirm_with_bracket", True))

    mouse_client = WindowsMouseClient()
    clicker = RequiemClicker(
        mouse_client,
        window_title_substring=window_title_substring,
        wait_for_backspace_on_init=wait_for_backspace_on_init,
    )

    if args.method == "sharpening_items_to":
        targets = _require(cfg, "targets")
        backpack_indices = _get_opt(cfg, "backpack_indices", None)
        clicker.sharpening_items_to(
            targets=targets,
            backpack_indices=backpack_indices,
            confirm_with_bracket=confirm_with_bracket,
        )
        return 0

    if args.method == "disassemble_items":
        retries = _require(cfg, "retries")
        clicker.disassemble_items(
            retries=retries,
            confirm_with_bracket=confirm_with_bracket,
        )
        return 0

    raise RuntimeError(f"Неизвестный method: {args.method}")


if __name__ == "__main__":
    raise SystemExit(main())
