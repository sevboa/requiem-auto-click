from __future__ import annotations

from pathlib import Path

from setuptools import find_packages, setup  # pyright: ignore[reportMissingImports]


def _read_readme() -> str:
    readme = Path(__file__).with_name("README.md")
    return readme.read_text(encoding="utf-8") if readme.exists() else ""


setup(
    name="requiem-auto-click",
    version="1.0.0",
    description="Requiem auto clicker (sharpen/disassemble) for Windows",
    long_description=_read_readme(),
    long_description_content_type="text/markdown",
    author="",
    license="MIT",
    packages=find_packages(exclude=("__pycache__",)),
    include_package_data=True,
    package_data={
        # Явно включаем ассеты в wheel (не только через MANIFEST.in)
        "requiem_auto_click.modules": ["**/*.png"],
        "requiem_auto_click.img": ["*.png"],
        "requiem_auto_click.gui.plugins.launcher_plugin": ["assets/*.png"],
        # Примеры конфигов (как стартовая точка для пользователя)
        "requiem_auto_click.configs": ["*.py"],
    },
    python_requires=">=3.10",
    install_requires=[
        "dxcam",
        "opencv-python",
        "numpy",
        "pywin32",
        "mss",
        # GUI (без git): ставим по zip-архиву тега v1.1.0
        "sa-ui-operations-base @ https://github.com/sevboa/sa-ui-operations-base/archive/refs/tags/v1.1.0.zip",
    ],
    entry_points={
        "console_scripts": [
            "requiem-auto-click=requiem_auto_click.modules.cli:main",
        ]
    },
)


