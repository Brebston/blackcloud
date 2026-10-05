# BlackCloud

Self-hosted хмара з акцентом на безпеку: файли, календар, пошта, чат, 2FA.
Стек: **Django 5.2 + DRF + Channels**, **React + TypeScript (Vite)**, **PostgreSQL**, **Redis**, **ClamAV**, **Postfix + Dovecot + Rspamd**, **Traefik**, усе в **Docker Compose**.

## Можливості

| Модуль | Що вміє |
|---|---|
| Акаунти | Вхід за логіном або email, Argon2id, 2FA (TOTP + 10 одноразових резервних кодів), блокування після 5 невдалих спроб, список пристроїв із віддаленим виходом, журнал подій безпеки, запрошення, налаштування (тема, мова, часовий пояс, приватність) |
| Файли | Будь-які типи файлів, чанкове завантаження з відновленням (до 10 ГБ на файл), шифрування AES-256-GCM на рівні застосунку, антивірус ClamAV, карантин, папки, пошук, перейменування й переміщення, кошик (30 днів), перегляд зображень, PDF, відео й аудіо |
| Перегляд | Мініатюри фото й PDF у списку та в режимі «сітка», повноекранний переглядач з масштабуванням (колесо, pinch, перетягування) і гортанням ← →, PDF у вбудованому переглядачі браузера на весь екран |
| Документи | Перегляд і редагування Word, Excel і PowerPoint прямо в браузері (ONLYOFFICE, вмикається в `.env`). Кожне збереження стає новою зашифрованою версією файлу, яка проходить антивірус |
| Квоти | Квота для кожного користувача, атомарне резервування місця (обійти паралельними завантаженнями неможливо), кошик теж враховується |
| Спільний доступ | Доступ користувачам (лише читання), публічні посилання з терміном дії, паролем і лімітом завантажень (у БД зберігається лише хеш токена) |
| Календар | Кілька календарів, повторювані події (RRULE), нагадування, спільний доступ (перегляд або редагування), імпорт і експорт .ics, приватна ICS-підписка |
| Пошта | Власний поштовий сервер (Postfix, Dovecot, Rspamd, DKIM, антиспам, антивірус), веб-пошта з безпечним рендерингом HTML, окремі паролі застосунків для IMAP/SMTP-клієнтів |
| Чат | Особисті та групові розмови, доставка в реальному часі (WebSocket), індикатор набору тексту, редагування й видалення, непрочитані, вкладення файлів зі сховища |
| Адмінка | Користувачі, квоти, блокування, скидання 2FA, запрошення, статистика, журнал аудиту, а також Django admin на прихованому шляху (лише з 2FA) |

## Архітектура

```
Інтернет ─► Traefik (TLS, HSTS, CSP, rate limit)
              ├─► frontend  (nginx, статичний React SPA)
              ├─► backend   (Django ASGI: REST API + WebSocket; usercontent.DOMAIN — лише перегляд файлів)
              └─► onlyoffice (office.DOMAIN, окремий origin)
                              ├─ PostgreSQL  ─┐
                              ├─ Redis        │  мережа internal
                              ├─ том objects (шифротекст) (без виходу
                              ├─ ClamAV       │   в інтернет)
                              ├─ Dovecot ◄────┤
                              └─ Postfix ◄────┘
             worker / beat (Celery): антивірус, очищення кошика, нагадування
             worker ─► thumbnailer (мережа thumbs: без секретів і інтернету)
             migrate (одноразово): міграції від власника БД, веб працює як bc_app
Інтернет ─► Postfix :25/465/587, Dovecot :993  (мережа mailnet)
```

## Швидкий старт (продакшн)

Вимоги: Linux-сервер, Docker 24+ з Compose v2, домен, відкриті порти 80, 443, 25, 465, 587 і 993.
DNS: окрім `DOMAIN` потрібні записи для `office.DOMAIN` (редактор документів) і `usercontent.DOMAIN` (перегляд файлів). Це окремі origin, щоб вміст файлів і редактор не мали доступу до сесії основного сайту.

```bash
git clone <repo> blackcloud && cd blackcloud
make secrets            # генерує ./secrets/* і .env
nano .env               # DOMAIN, ACME_EMAIL, MAIL_DOMAIN, MAIL_HOSTNAME, ADMIN_URL_PREFIX
make lock && make pin   # (рекомендовано) залежності з хешами та образи за digest
make init               # збірка, migrate (окремий сервіс), запуск
make superuser          # перший адміністратор
```

Міграції вже в репозиторії. Після першої збірки закомітьте `frontend/package-lock.json`.

Відкрийте `https://DOMAIN`, увійдіть адміністратором і **одразу увімкніть 2FA** (Налаштування → Безпека): без неї адмін-функції заблоковані.

### DNS для пошти

| Запис | Значення |
|---|---|
| `A mail.example.com` | IP сервера |
| `MX example.com` | `10 mail.example.com` |
| `TXT example.com` (SPF) | `v=spf1 mx -all` |
| `TXT mail._domainkey.example.com` (DKIM) | вивід `make dkim` |
| `TXT _dmarc.example.com` | `v=DMARC1; p=quarantine; rua=mailto:postmaster@example.com` |
| PTR (у хостера) | `mail.example.com` |

TLS-сертифікат для пошти покладіть у `certs/fullchain.pem` і `certs/privkey.pem` (наприклад, через certbot). Без нього Postfix і Dovecot стартують із самопідписаним сертифікатом.

> Багато VPS-провайдерів блокують вихідний порт 25: перевірте це до запуску. Публічні RBL (Spamhaus) не відповідають на запити через публічні DNS-резолвери, тож для повноцінного антиспаму варто підняти локальний резолвер (наприклад, unbound).

## Локальна розробка

```bash
make secrets
# у .env: DOMAIN=localhost, TLS_RESOLVER= (порожньо), SNI_STRICT=false, DJANGO_DEBUG=true
# /etc/hosts (Safari/Firefox; Chrome резолвить *.localhost сам):
#   127.0.0.1 office.localhost usercontent.localhost
make up-dev             # https://localhost (самопідписаний сертифікат)
# Самопідписаний сертифікат треба один раз прийняти для кожного хоста:
#   https://localhost, https://office.localhost/healthcheck, https://usercontent.localhost
```

Фронтенд з hot-reload: `cd frontend && npm install && npm run dev` (API проксіюється на `https://localhost`).

## Тести

```bash
make test
```

Тести покривають: вхід і блокування, 2FA (зокрема повторне використання TOTP і одноразовість резервних кодів), запрошення, права адміністратора, атомарність квот, шифрування й цілісність чанків, ізоляцію файлів між користувачами, спільний доступ, кошик, публічні посилання (пароль, ліміт, підпис), санітизацію імен і HTML листів, календар і чат.

## Онлайн-редактор документів (ONLYOFFICE)

`make secrets` додає в `.env` рядки `OFFICE_ENABLED=true`, `COMPOSE_PROFILES=office` та випадковий `OFFICE_JWT_SECRET`. Після цього:

```bash
docker compose up -d --build
```

Document Server займає ~2–3 ГБ RAM і стартує 1–2 хвилини. Він доступний через Traefik на піддомені `office.DOMAIN`, бачить лише backend (мережа `edge`), а запити підписуються JWT. Щоб вимкнути редактор, постав `OFFICE_ENABLED=false` і прибери `office` з `COMPOSE_PROFILES`.

Мініатюри для файлів, завантажених до оновлення: `make thumbnails`.

## Корисні команди

| Команда | Дія |
|---|---|
| `make logs` | логи всіх сервісів |
| `make dkim` | DKIM-запис для DNS |
| `make backup` | дамп PostgreSQL у `backups/` |
| `make migrate` | міграції (одноразовий сервіс від імені власника БД) |
| `make lock` / `make pin` | залежності з хешами / образи за digest |
| `make scan` / `make audit` | сканування образів (Trivy) / перевірка залежностей |
| `make shell` | Django shell |

**Бекап** = дамп БД + том `objects_data` + том `mail_data` + **каталог `secrets/`**. Без `file_master_keys` файли розшифрувати неможливо: зберігайте копію ключів окремо від даних.

## Структура

```
backend/            Django: apps/{core,accounts,storage,calendars,chat,mail}, tests/
frontend/           React SPA (Vite + TS), nginx.conf
mailserver/         postfix/, dovecot/, rspamd/, clamav/, postgres-init/
traefik/dynamic/    маршрути, заголовки безпеки, TLS
scripts/            init-secrets.sh
```

Модель загроз і деталі захисту описані в [SECURITY.md](SECURITY.md).
