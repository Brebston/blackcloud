"""SQL-представлення для Postfix/Dovecot.

Поштові сервіси підключаються до БД окремою роллю `mailreader`, яка має право
лише на SELECT з цих трьох представлень — і ні до чого більше.
"""

import logging

from django.db import connection

logger = logging.getLogger("blackcloud")

VIEWS_SQL = [
    """
    CREATE OR REPLACE VIEW mail_domains_v AS
      SELECT name AS domain FROM mail_maildomain WHERE active
    """,
    """
    CREATE OR REPLACE VIEW mail_mailboxes_v AS
      SELECT lower(m.local_part || '@' || d.name) AS address,
             NULLIF(m.client_password_hash, '') AS password,
             m.quota_mb AS quota_mb,
             lower(m.local_part) AS local_part,
             d.name AS domain
        FROM mail_mailbox m
        JOIN mail_maildomain d ON d.id = m.domain_id
        JOIN accounts_user u ON u.id = m.user_id
       WHERE m.active AND d.active AND u.is_active
    """,
    """
    CREATE OR REPLACE VIEW mail_aliases_v AS
      SELECT a.source AS source, a.destination AS destination
        FROM mail_alias a
       WHERE a.active
    """,
]

GRANT_SQL = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mailreader') THEN
    GRANT SELECT ON mail_domains_v, mail_mailboxes_v, mail_aliases_v TO mailreader;
  END IF;
END $$;
"""


def create_mail_views(sender=None, **kwargs):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for sql in VIEWS_SQL:
            cursor.execute(sql)
        cursor.execute(GRANT_SQL)
    logger.info("mail SQL views ensured")
