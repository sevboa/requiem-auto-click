from __future__ import annotations

import sys

# pylint: disable=import-error,no-name-in-module
from PySide6.QtWidgets import QApplication
from sa_ui_operations import IntegerSetting, MainWindow, PluginRegistry, StringSetting

from .constants import (
    APP_NAME,
    LAUNCHER_COMMAND_SETTING_KEY,
    ORG_NAME,
    REFRESH_INTERVAL_SECONDS_SETTING_KEY,
)
from .plugins.capture_roi_plugin import CaptureRoiPlugin
from .plugins.launcher_plugin import LauncherPlugin
from .plugins.mailbox_plugin import MailboxPlugin
from .plugins.sharpening_plugin import SharpeningPlugin
from .plugins.disassemble_plugin import DisassemblePlugin


def run_gui(argv: list[str] | None = None) -> int:
    _ = argv

    registry = PluginRegistry()
    registry.register(LauncherPlugin())
    registry.register(CaptureRoiPlugin())
    registry.register(MailboxPlugin())
    registry.register(SharpeningPlugin())
    registry.register(DisassemblePlugin())

    global_settings = [
        StringSetting(
            key=LAUNCHER_COMMAND_SETTING_KEY,
            label="Путь к exe + аргументы лаунчера",
            default_value="",
            description=(
                "Пример: {путь к папке}\\Requiem2.exe 128.241.93.208 -FromLauncher 0/0 0 0 7 3 0"
            ),
        ),
        IntegerSetting(
            key=REFRESH_INTERVAL_SECONDS_SETTING_KEY,
            label="Частота обновления списка окон (сек)",
            default_value=10,
            description="Рекомендуется не меньше 1 секунды (минимум = 1).",
        ),
    ]

    app = QApplication(sys.argv)
    window = MainWindow(registry, ORG_NAME, APP_NAME, global_settings)
    window.show()
    return int(app.exec())

