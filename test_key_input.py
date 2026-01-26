"""
Тест ввода текста через SendInput (KEYEVENTF_UNICODE).

Запуск:
  python test_key_input.py

После старта есть 5 секунд, чтобы сфокусировать нужное окно.
"""

from __future__ import annotations

import time

from requiem_auto_click.modules.keyboard_utils import type_text, press_key_combo


def main() -> None:
    print("Через 5 секунд будет введено: 'Hello'. Переключись на нужное окно.")
    time.sleep(5.0)
    # type_text("Hello")
    press_key_combo(["Num1"])
    print("Готово.")


if __name__ == "__main__":
    main()

