"""Службові файли macOS: AppleDouble («._ім'я») і .DS_Store.

Finder через WebDAV зберігає в них розширені атрибути (мітки, коментарі, quarantine)
і читає їх назад, тому WebDAV їх приймає і показує. Скрізь інше — вебінтерфейс,
пошук, кошик, ZIP-архіви, мобільна версія — вони невидимі.

AppleDouble-«супутник» `._X` супроводжує свій файл `X` у тій самій папці: веб-дії
з `X` (видалення, перейменування, переміщення, відновлення, остаточне видалення)
застосовуються і до нього, а «осиротілі» супутники прибирає періодичне завдання.
"""

from django.db.models import Q
from django.utils import timezone

APPLEDOUBLE_PREFIX = "._"
SYSTEM_NAMES = (".DS_Store",)


def is_system_name(name: str) -> bool:
    return name.startswith(APPLEDOUBLE_PREFIX) or name in SYSTEM_NAMES


def system_q() -> Q:
    return Q(name__startswith=APPLEDOUBLE_PREFIX) | Q(name__in=SYSTEM_NAMES)


def hide_system(qs):
    """Прибирає службові файли macOS з вибірки File."""
    return qs.exclude(system_q())


def companion_name(name: str) -> str:
    return APPLEDOUBLE_PREFIX + name


def _companions(owner_id, folder_id, name: str, *, trashed: bool):
    from .models import File

    if is_system_name(name):
        return File.objects.none()
    return File.objects.filter(
        owner_id=owner_id, folder_id=folder_id, name=companion_name(name), deleted_at__isnull=not trashed
    )


def trash_companion(owner_id, folder_id, name: str, deleted_at) -> None:
    """Файл пішов у кошик — його супутник теж (з тим самим часом, щоб відновлювались разом)."""
    _companions(owner_id, folder_id, name, trashed=False).update(deleted_at=deleted_at)


def move_companion(owner_id, old_folder_id, old_name: str, new_folder_id, new_name: str) -> None:
    """Перейменування/переміщення файлу у вебінтерфейсі — супутник іде слідом.
    Якщо в цілі вже є супутник з таким ім'ям, старий іде в кошик (новий не перезаписуємо)."""
    from .models import File

    if (old_folder_id, old_name) == (new_folder_id, new_name):
        return
    target_name = companion_name(new_name)
    taken = File.objects.filter(
        owner_id=owner_id, folder_id=new_folder_id, name=target_name, deleted_at__isnull=True
    ).exists()
    qs = _companions(owner_id, old_folder_id, old_name, trashed=False)
    if taken:
        qs.update(deleted_at=timezone.now())
    else:
        qs.update(folder_id=new_folder_id, name=target_name, updated_at=timezone.now())


def restore_companion(f, old_folder_id, old_name: str, deleted_at) -> None:
    """Відновлення з кошика: супутник, видалений разом із файлом, повертається поруч із ним."""
    from .models import File

    qs = _companions(f.owner_id, old_folder_id, old_name, trashed=True).filter(deleted_at=deleted_at)
    target_name = companion_name(f.name)
    if File.objects.filter(owner_id=f.owner_id, folder_id=f.folder_id, name=target_name, deleted_at__isnull=True).exists():
        return
    qs.update(deleted_at=None, folder_id=f.folder_id, name=target_name)


def trashed_companions(f):
    """Супутники файлу в кошику (для остаточного видалення разом із файлом)."""
    return _companions(f.owner_id, f.folder_id, f.name, trashed=True).filter(deleted_at=f.deleted_at)
