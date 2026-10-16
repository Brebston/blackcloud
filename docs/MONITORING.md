## Моніторинг і сповіщення в Telegram (Uptime Kuma)

Сповіщення: «сайт недоступний», «диск заповнено на 85 %», «ClamAV не відповідає», «сертифікат скоро закінчиться», «пошта не приймає з'єднання», «Celery/beat зупинились».

### Як це влаштовано

- **`GET /api/health/`** — простий публічний статус (лише БД), як і раніше.
- **`GET /api/health/deep/`** — глибока перевірка, доступна лише з заголовком
  `Authorization: Bearer <токен>` (секрет `secrets/health_token`). Без секрету ендпоінт вимкнено (404),
  з неправильним токеном — 403, понад 30 запитів/хв з однієї IP — 429.
  Відповідає **200**, якщо все гаразд, і **503**, якщо хоч одна перевірка не пройшла:

  ```json
  {"status": "fail", "checks": {
    "db": {"ok": true}, "cache": {"ok": true},
    "clamav": {"ok": false, "enabled": true},
    "disk": {"ok": true, "percent": 61.2, "free_gb": 350.4, "threshold": 85},
    "cert_web": {"ok": true, "days_left": 71, "not_after": "2026-12-22"},
    "cert_mail": {"ok": true, "days_left": 71, "not_after": "2026-12-22"},
    "celery": {"ok": true, "age_seconds": 34}}}
  ```

  | Перевірка | Що перевіряє | Коли «fail» |
  |---|---|---|
  | `db` | `SELECT 1` у PostgreSQL | БД недоступна |
  | `cache` | запис/читання ключа в Redis | Redis недоступний |
  | `clamav` | PING до clamd | антивірус увімкнений, але не відповідає |
  | `disk` | заповнення диска з файлами (`/data/objects`) | ≥ `HEALTH_DISK_ALERT_PERCENT` (85 %) |
  | `cert_web` | сертифікат, який Traefik віддає для `DOMAIN` | лишилось < `HEALTH_CERT_MIN_DAYS` (14) днів |
  | `cert_mail` | сертифікат Dovecot (IMAPS 993) для `MAIL_HOSTNAME` | < 14 днів |
  | `celery` | мітка, яку beat→worker оновлює щохвилини | старіша за 5 хв |

  Пороги змінюються в `.env`: `HEALTH_DISK_ALERT_PERCENT=85`, `HEALTH_CERT_MIN_DAYS=14`,
  `HEALTH_CELERY_MAX_AGE=300`. У відповіді немає адрес, текстів помилок і секретів.
- **Uptime Kuma** — контейнер `uptime-kuma` (профіль `monitoring`), який щохвилини опитує сайт і
  надсилає сповіщення в Telegram. Його веб-інтерфейс слухає лише `127.0.0.1:3001` на сервері.
  Контейнер звертається до `DOMAIN` напряму через Traefik (запис у `extra_hosts`), тож NAT hairpin
  на роутері не потрібен.

> **Обмеження.** Монітор на тому ж сервері не повідомить, якщо сервер вимкнувся повністю
> (світло, інтернет у провайдера, завис хост) — нікому буде надсилати. Додайте ще одну
> **безкоштовну зовнішню перевірку** (UptimeRobot, Better Stack або healthchecks.io) на
> `https://brebston.com/api/health/` з інтервалом 5 хв і сповіщенням в Telegram/e-mail.

### 1. Увімкнення

На сервері (`/opt/blackcloud`):

```sh
make secrets          # створить secrets/health_token (наявні секрети не змінюються)
```

> Це обов'язково **до** першого деплою з цією версією: backend монтує секрет `health_token`,
> і без файлу `secrets/health_token` контейнер не створиться.

Додайте `monitoring` у `COMPOSE_PROFILES` у `.env`, напр.:

```
COMPOSE_PROFILES=office,mailcert,monitoring
```

і запустіть (або дочекайтесь автодеплою — він теж виконує `docker compose up -d`):

```sh
docker compose up -d
```

Перевірка ендпоінта з сервера:

```sh
TOKEN=$(sudo cat /opt/blackcloud/secrets/health_token)
curl -s -H "Authorization: Bearer $TOKEN" --resolve brebston.com:443:127.0.0.1 https://brebston.com/api/health/deep/
```

### 2. Перший вхід у Kuma

З вашого комп'ютера відкрийте SSH-тунель:

```sh
ssh -L 3001:127.0.0.1:3001 brebston@blkbox
```

і перейдіть на <http://localhost:3001>. При першому вході Kuma попросить створити обліковий
запис адміністратора — задайте довгий пароль. Далі **Settings → Security** → увімкніть 2FA.

### 3. Telegram-бот

1. У Telegram напишіть **@BotFather** → `/newbot` → назва і username (має закінчуватись на `bot`).
   Збережіть **токен** вигляду `123456789:AA...`.
2. Напишіть своєму боту будь-яке повідомлення (або додайте його в групу і напишіть там).
3. Дізнайтесь **chat id**: відкрийте `https://api.telegram.org/bot<TOKEN>/getUpdates` і знайдіть
   `"chat":{"id":...}` (для груп id від'ємний), або напишіть **@userinfobot** — він відповість вашим id.
4. У Kuma: **Settings → Notifications → Setup Notification** → тип **Telegram** → Bot Token і
   Chat ID → **Test** (має прийти повідомлення) → увімкніть **Default enabled** та
   **Apply on all existing monitors** → Save.

### 4. Монітори

**Add New Monitor** для кожного (інтервал 60 с, Retries 2–3 — щоб не було хибних тривог під час деплою):

| # | Тип | Назва | Налаштування |
|---|---|---|---|
| 1 | HTTP(s) | Сайт | URL `https://brebston.com`; **Certificate Expiry Notification** — увімкнено (у Settings → Notifications → TLS Certificate Expiry залиште 14/7 днів) |
| 2 | HTTP(s) | Стан сервісів | URL `https://brebston.com/api/health/deep/`; Method GET; **Headers**: `{"Authorization": "Bearer <токен>"}`, токен — `sudo cat /opt/blackcloud/secrets/health_token`; Accepted Status Codes `200-299`. Покриває диск, ClamAV, БД, Redis, сертифікати сайту й пошти, Celery |
| 3 | TCP Port | SMTP 25 | Host `host.docker.internal`, Port `25` |
| 4 | TCP Port | SMTPS 465 | Host `host.docker.internal`, Port `465` |
| 5 | TCP Port | Submission 587 | Host `host.docker.internal`, Port `587` |
| 6 | TCP Port | IMAPS 993 | Host `host.docker.internal`, Port `993` |
| 2a | HTTP(s) | Диск ≥ 85% | URL `https://brebston.com/api/health/deep/?only=disk`; ті самі **Headers**; Accepted Status Codes `200-299`. Окрема тривога з назвою «Диск ≥ 85%» (поріг — `HEALTH_DISK_ALERT_PERCENT`); інтервал можна 300 с |
| 2b | HTTP(s) | Резервні копії | URL `https://brebston.com/api/health/deep/?only=backup`; ті самі **Headers**. Тривога, якщо немає вдалої копії понад 26 год |
| 7 | DNS (необов'язково) | DNS | Hostname `brebston.com`, Resolver `1.1.1.1`, Record A — перевіряє, що домен резолвиться (і зміну IP, якщо задати очікуване значення) |

Щоб у Telegram одразу було видно **що саме** зламалось, монітор №2 можна зробити типу
**HTTP(s) – Json Query**: Json Query `status`, Expected Value `ok`. Kuma покаже у сповіщенні
фактичну відповідь. Альтернатива — кілька Json Query-моніторів на ту ж адресу, напр.
`checks.disk.ok` = `true`, `checks.clamav.ok` = `true` (окремі тривоги «диск», «ClamAV»).

Параметр `?only=` приймає назви перевірок через кому: `db`, `cache`, `clamav`, `disk`, `cert_web`,
`cert_mail`, `celery`, `backup` (невідома назва → 400). Так кожна важлива тривога приходить
у Telegram з власною назвою монітора, а не загальним «Стан сервісів — Down».

Подивитися все одразу (диск, пам'ять, процесор, черги антивірусу й мініатюр, резервні копії,
сертифікати, служби) можна в **Адмінка → Стан сервера**. Ця сторінка сповіщень не надсилає —
їх надсилає Kuma за моніторами вище.

Якщо TCP-монітори на `host.docker.internal` не проходять, а ззовні порти відкриті (буває через
правила файрволу хоста), вкажіть замість нього LAN-адресу сервера (напр. `192.168.1.10`).

### 5. Перевірка сповіщень

- **Тест каналу:** Settings → Notifications → Telegram → **Test**.
- **ClamAV:** `docker compose stop clamav` → через 1–3 хв монітор «Стан сервісів» стане DOWN
  (`clamav.ok=false`) і прийде повідомлення; `docker compose start clamav` → прийде «UP».
  Після старту ClamAV завантажує бази 1–2 хв.
- **Диск:** тимчасово `HEALTH_DISK_ALERT_PERCENT=1` у `.env` → `docker compose up -d backend` →
  тривога «Диск ≥ 85%» (і червоний диск в Адмінка → Стан сервера); поверніть 85 і знову `docker compose up -d backend`.
- **Сайт:** `docker compose stop frontend` → DOWN монітора «Сайт»; потім `docker compose start frontend`.
- **Пошта:** `docker compose stop dovecot` → DOWN «IMAPS 993» і `cert_mail` у «Стан сервісів».

### 6. (Необов'язково) Доступ до Kuma через status.DOMAIN

За замовчуванням Kuma не публікується. Якщо потрібна публічна сторінка статусу — додайте
DNS-запис `status.brebston.com` і в `traefik/dynamic/` окремий router `Host(\`status.brebston.com\`)`
→ service `http://uptime-kuma:3001` з `tls.certResolver`. Обов'язково: сильний пароль і 2FA в Kuma,
а для адмін-частини краще обмежити доступ (IP allowlist/basic-auth middleware) і відкривати
лише Status Page. Без потреби цього не робіть — SSH-тунель безпечніший.

### Змінні та секрети

- Секрет `secrets/health_token` (створює `make secrets`; змінити — видалити файл, `make secrets`,
  `docker compose up -d backend` і оновити заголовок у Kuma).
- `.env`: `HEALTH_DISK_ALERT_PERCENT` (85), `HEALTH_CERT_MIN_DAYS` (14), `HEALTH_CELERY_MAX_AGE` (300),
  `HEALTH_TLS_HOST` (172.31.250.2 — Traefik), `HEALTH_MAIL_TLS_HOST` (dovecot; порожньо — не перевіряти),
  `HEALTH_MAIL_TLS_PORT` (993), `UPTIME_KUMA_IMAGE` (louislam/uptime-kuma:1.23.16).
- Дані Kuma — том `uptime_kuma_data` (варто включити в бекап).
