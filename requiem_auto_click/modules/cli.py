"""CLI entrypoint for requiem-auto-click.

This module exists so the project can be installed and run via `pip install ...`
with a console entry point (see setup.py).
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from .requiem_clicker import RequiemClicker
from .windows_mouse_client import WindowsMouseClient


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
        "--gui",
        action="store_true",
        help="Run GUI mode (sa-ui-operations-base).",
    )
    p.add_argument(
        "method",
        nargs="?",
        choices=["init", "sharpening_items_to", "disassemble_items"],
        help="Which scenario to run.",
    )
    p.add_argument(
        "--config",
        required=False,
        help="Path to a .py config file (targets/retries and optional settings).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files when running init.",
    )
    return p


def _run_init(*, force: bool) -> int:
    """
    Копирует примеры конфигов в текущую директорию, чтобы пользователь мог быстро стартовать.
    """
    try:
        from requiem_auto_click import configs  # type: ignore
    except Exception as e:
        raise RuntimeError(f"Не удалось импортировать пакет configs (примеры конфигов). Ошибка: {e}") from e

    src_dir = Path(configs.__file__).resolve().parent
    pairs = [
        (src_dir / "example_disassemble.py", Path.cwd() / "disassemble.py"),
        (src_dir / "example_sharpening.py", Path.cwd() / "sharpening.py"),
    ]

    for src, dst in pairs:
        if not src.exists():
            raise FileNotFoundError(f"Example config not found inside installed package: {src}")
        if dst.exists() and not force:
            raise FileExistsError(f"File already exists: {dst}. Use --force to overwrite.")
        shutil.copyfile(src, dst)

    # Use ASCII-only output to avoid mojibake in some Windows consoles.
    print("Created config files:")
    print("  ./disassemble.py")
    print("  ./sharpening.py")
    print("")
    print("Run:")
    print("  requiem-auto-click disassemble_items --config ./disassemble.py")
    print("  requiem-auto-click sharpening_items_to --config ./sharpening.py")
    print("")
    print("Edit:")
    print("  - disassemble.py: change `retries`")
    print("  - sharpening.py:  change `targets`")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if bool(getattr(args, "gui", False)):
        try:
            from requiem_auto_click.gui.app import run_gui

            return int(run_gui())
        except ModuleNotFoundError as e:
            # Если пользователь запускает GUI из исходников без установки зависимостей
            # (или установка прервалась), дадим понятную ошибку.
            if getattr(e, "name", "") in {"sa_ui_operations", "PySide6"}:
                raise SystemExit(
                    "GUI-зависимости не установлены. Установите зависимости и повторите:\n"
                    "  python -m pip install -r requirements.txt"
                ) from e
            raise

    if not getattr(args, "method", None):
        # argparse won't enforce required positional when nargs="?"
        raise SystemExit("Ошибка: укажите method (init/sharpening_items_to/disassemble_items) или используйте --gui")

    if args.method == "init":
        return _run_init(force=bool(getattr(args, "force", False)))

    if not args.config:
        raise SystemExit("Ошибка: для этого метода нужен параметр --config <path_to_config.py>")

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


