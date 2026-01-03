from __future__ import annotations

from pathlib import Path

from setuptools import find_packages, setup  # pyright: ignore[reportMissingImports]


def _read_readme() -> str:
    readme = Path(__file__).with_name("README.md")
    return readme.read_text(encoding="utf-8") if readme.exists() else ""


setup(
    name="requiem-auto-click",
    version="0.1.0",
    description="Requiem auto clicker (sharpen/disassemble) for Windows",
    long_description=_read_readme(),
    long_description_content_type="text/markdown",
    author="",
    license="MIT",
    packages=find_packages(exclude=("__pycache__",)),
    include_package_data=True,
    package_data={
        # Явно включаем ассеты в wheel (не только через MANIFEST.in)
        "modules": ["**/*.png"],
        "img": ["*.png"],
        # Примеры конфигов (как стартовая точка для пользователя)
        "configs": ["*.py"],
    },
    python_requires=">=3.10",
    install_requires=[
        "dxcam",
        "opencv-python",
        "numpy",
        "pywin32",
        "mss",
    ],
    entry_points={
        "console_scripts": [
            "requiem-clicker=modules.cli:main",
        ]
    },
)


