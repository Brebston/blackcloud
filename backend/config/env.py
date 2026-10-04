"""Читання конфігурації з оточення та Docker secrets.

Секрет NAME шукається у такому порядку:
  1. файл зі змінної NAME_FILE
  2. /run/secrets/<name у нижньому регістрі>
  3. змінна оточення NAME
"""

import os
from pathlib import Path

SECRETS_DIR = Path(os.environ.get("SECRETS_DIR", "/run/secrets"))


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def read_secret(name: str, default: str | None = None) -> str | None:
    file_var = os.environ.get(f"{name}_FILE")
    candidates = [Path(file_var)] if file_var else []
    candidates.append(SECRETS_DIR / name.lower())
    for path in candidates:
        try:
            return path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            continue
    return os.environ.get(name, default)
