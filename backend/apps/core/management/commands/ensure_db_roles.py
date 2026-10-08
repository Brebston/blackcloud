"""Створює/оновлює обмежену роль PostgreSQL для застосунку.

Запускається одноразовим сервісом `migrate` від імені власника БД ПІСЛЯ міграцій.
Веб, воркер і beat підключаються як `bc_app`: лише SELECT/INSERT/UPDATE/DELETE
на таблицях застосунку — без CREATE/ALTER/DROP, без суперкористувача, без
COPY ... TO PROGRAM. SQL-ін'єкція (якби вона знайшлася) не дасть виконати
команди на сервері БД чи змінити схему.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from config.env import env, read_secret


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Command(BaseCommand):
    help = "Створює роль bc_app з мінімальними правами (лише DML)"

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write("not postgresql — skipped")
            return
        role = env("APP_DB_USER", "bc_app")
        password = read_secret("APP_DB_PASSWORD")
        if not password:
            raise CommandError("Секрет app_db_password не задано (make secrets)")
        q = connection.ops.quote_name
        db = q(settings.DATABASES["default"]["NAME"])
        owner = q(settings.DATABASES["default"]["USER"])
        r = q(role)
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
            verb = "ALTER" if cur.fetchone() else "CREATE"
            cur.execute(
                f"{verb} ROLE {r} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                f"NOBYPASSRLS INHERIT PASSWORD {_literal(password)}"
            )
            statements = [
                f"GRANT CONNECT ON DATABASE {db} TO {r}",
                f"REVOKE CREATE ON SCHEMA public FROM {r}",
                f"GRANT USAGE ON SCHEMA public TO {r}",
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}",
                f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {r}",
                # Нові таблиці з майбутніх міграцій отримають ті самі права автоматично
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
                f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {r}",
                # Історію міграцій змінює лише власник схеми
                f"REVOKE INSERT, UPDATE, DELETE ON django_migrations FROM {r}",
            ]
            for sql in statements:
                cur.execute(sql)
        self.stdout.write(self.style.SUCCESS(f"role {role}: {'created' if verb == 'CREATE' else 'updated'}, DML-only grants applied"))
