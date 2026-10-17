## Веб-пошта: групові дії, «З зірочкою», блокування відправників

- **Аватари відправників** — літера на кольорі (детермінований за адресою) або фото локального користувача BlackCloud (`GET /api/users/avatars/?emails=…`, один пакетний запит на сторінку списку).
- **Вибір кількох листів** — прапорці (на телефоні — дотик по аватару), «вибрати всі на сторінці»; групові дії: видалити (у «Кошику» — назавжди), архів, перемістити, спам / не спам, прочитано / непрочитано, зірочка, заблокувати відправників. API: `POST /api/mail/messages/bulk/` `{folder, uids[≤500], action, target?}` — одна IMAP-сесія, набори UID.
- **«З зірочкою»** — віртуальна тека (`GET /api/mail/messages/?folder=__starred__`): позначені листи з усіх тек, крім «Кошика» і «Спаму», новіші першими; переглядається щонайбільше 1000 найновіших позначених листів.
- **Заблоковані відправники** — меню листа «⋯ → Заблокувати …» або Налаштування → Пошта. Можна вказати точну адресу або цілий домен (`@example.com`). Наявні листи від них у «Вхідних» одразу переносяться у «Спам»; нові — завданням Celery beat `mail-enforce-blocklist` щохвилини (worker входить у Dovecot як master-user, окремих налаштувань не потрібно). Обмеження: новий лист може бути видно у «Вхідних» до ~1 хв (зокрема в IMAP-клієнтах); push-сповіщення про такі листи не надсилаються. Ліміт — 1000 записів на скриньку. API: `GET/POST /api/mail/blocked/`, `DELETE /api/mail/blocked/<id>/`.

## MTA-STS і TLS-RPT

**MTA-STS** (RFC 8461) каже іншим поштовим серверам (Gmail, Outlook, Yahoo…), що пошту для домену треба доставляти **лише через TLS з дійсним сертифікатом** на `MAIL_HOSTNAME`. Без нього шифрування SMTP «опортуністичне»: зловмисник посередині може прибрати STARTTLS і прочитати лист. **TLS-RPT** (RFC 8460) — щоденні звіти від відправників про проблеми з TLS на адресу `tlsrpt@<MAIL_DOMAIN>`.

Як влаштовано:
- Політика генерується backend-ом (`apps/mail/mta_sts.py`) і віддається лише за адресою `https://mta-sts.<MAIL_DOMAIN>/.well-known/mta-sts.txt`; на цьому хості інших шляхів немає (Traefik → 404).
- Маршрут і сертифікат Let's Encrypt для `mta-sts.<MAIL_DOMAIN>` Traefik створює, лише коли задано `MTA_STS_MODE`.
- `id` у TXT `_mta-sts` — хеш тексту політики: змінили режим, MX чи `max_age` → `make mta-sts` покаже новий `id`, його треба оновити в DNS (інакше відправники тримають стару політику до `max_age`).
- Вихідна пошта від нас MTA-STS чужих доменів не перевіряє (Postfix без `postfix-mta-sts-resolver`) — це окрема річ, на вхідну не впливає.

### Увімкнення

1. **DNS:** A-запис `mta-sts.<MAIL_DOMAIN>` → IP сервера (Cloudflare: «DNS only»). Дочекайтеся, поки `dig +short mta-sts.<MAIL_DOMAIN>` поверне IP — інакше Let's Encrypt не видасть сертифікат.
2. **`.env`:** `MTA_STS_MODE=testing`, потім `docker compose up -d traefik backend` (або деплой).
3. **Псевдонім для звітів:** `docker compose exec backend python manage.py mail_service_aliases --to <ваш логін>` — додасть `tlsrpt@` (наявні псевдоніми не змінюються).
4. **`make mta-sts`** — покаже два TXT-записи, додайте їх у DNS:
   - `_mta-sts.<MAIL_DOMAIN>` → `v=STSv1; id=…`
   - `_smtp._tls.<MAIL_DOMAIN>` → `v=TLSRPTv1; rua=mailto:tlsrpt@<MAIL_DOMAIN>`
5. **Перевірка:**
   ```bash
   curl -s https://mta-sts.example.com/.well-known/mta-sts.txt
   dig +short TXT _mta-sts.example.com
   dig +short TXT _smtp._tls.example.com
   ```
   Онлайн: [Hardenize](https://www.hardenize.com) або [MXToolbox MTA-STS](https://mxtoolbox.com/mta-sts.aspx).
6. **Через 2–4 тижні**, якщо звіти TLS-RPT (приходять на `tlsrpt@`, вкладення `.json.gz`) не показують помилок (`"total-failure-session-count": 0`), — `MTA_STS_MODE=enforce`, деплой, **оновіть `id`** з `make mta-sts`.

### Режими й змінні

| Змінна | Типово | Опис |
|---|---|---|
| `MTA_STS_MODE` | порожньо (вимкнено) | `testing` — лише звіти; `enforce` — пошта без дійсного TLS не доставляється; `none` — відкликати політику (лишіть так на `max_age`, потім можна вимкнути) |
| `MTA_STS_MX` | `MAIL_HOSTNAME` | Дозволені MX через кому (можна `*.example.com`) — мають збігатися з MX-записами |
| `MTA_STS_MAX_AGE` | 86400 (testing/none), 604800 (enforce) | Скільки секунд відправники кешують політику |
| `TLSRPT_RUA` | `mailto:tlsrpt@<MAIL_DOMAIN>` | Куди надсилати звіти TLS-RPT |

### Ризики в режимі enforce

- Якщо сертифікат `MAIL_HOSTNAME` протермінується або не відповідатиме імені, відправники з MTA-STS **не доставлятимуть** пошту (листи чекатимуть у їхніх чергах і повернуться через кілька днів). Сертифікат оновлює Traefik + certdumper; стежте за ним у «Стані сервера» і в Uptime Kuma.
- Зміна MX (новий хост) — спершу додайте його в `MTA_STS_MX`, оновіть `id`, і лише потім міняйте MX.
- Варто додати в Uptime Kuma монітор HTTP(s) — Keyword на `https://mta-sts.<MAIL_DOMAIN>/.well-known/mta-sts.txt` зі словом `STSv1`.
