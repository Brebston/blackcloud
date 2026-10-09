# Brebston Cloud (BlackCloud)

Self-hosted хмара з акцентом на безпеку: файли, календар, пошта, чат, 2FA. Працює на власному сервері (зокрема домашньому за NAT) і розгортається однією командою Docker Compose.

## Технології

| Шар | Що використовується |
|---|---|
| Бекенд | Python 3.12, Django 5.2, Django REST Framework, Channels (WebSocket), Celery + beat |
| Фронтенд | React 18, TypeScript, Vite, власні CSS-змінні (теми й акценти), без UI-бібліотек |
| Дані | PostgreSQL 16, Redis, зашифроване сховище файлів (Docker-том або S3) |
| Пошта | Postfix, Dovecot, Rspamd (антиспам, DKIM), ClamAV |
| Інфраструктура | Traefik v3 (TLS Let's Encrypt, заголовки безпеки), Docker Compose, GitHub Actions |

## Можливості

| Модуль | Що вміє |
|---|---|
| Акаунти | Вхід за логіном або email, Argon2id, 2FA (TOTP + 10 одноразових резервних кодів), блокування після 5 невдалих спроб, список пристроїв із віддаленим виходом, журнал подій безпеки, запрошення, налаштування (часовий пояс, приватність) |
| Оформлення | Світла, темна або системна тема, 8 акцентних кольорів, перемикач мови (українська / English). Вибір застосовується одразу і зберігається в акаунті та браузері |
| Файли | Будь-які типи файлів, чанкове завантаження з відновленням (до 10 ГБ на файл), шифрування AES-256-GCM на рівні застосунку, антивірус ClamAV, карантин, папки, пошук, перейменування й переміщення, кошик (30 днів), перегляд зображень, PDF, відео й аудіо |
| Перегляд | Мініатюри фото й PDF у списку та в режимі «сітка», повноекранний переглядач з масштабуванням (колесо, pinch, перетягування) і гортанням ← →, PDF у вбудованому переглядачі браузера на весь екран |
| Документи | Перегляд і редагування Word, Excel і PowerPoint прямо в браузері (ONLYOFFICE, вмикається в `.env`). Кожне збереження стає новою зашифрованою версією файлу, яка проходить антивірус |
| Квоти | Квота для кожного користувача, атомарне резервування місця (обійти паралельними завантаженнями неможливо), кошик теж враховується |
| Спільний доступ | Доступ користувачам (лише читання), публічні посилання з терміном дії, паролем і лімітом завантажень (у БД зберігається лише хеш токена) |
| Календар | Кілька календарів, повторювані події (RRULE), нагадування, спільний доступ (перегляд або редагування), імпорт і експорт .ics, приватна ICS-підписка |
| Пошта | Власний поштовий сервер (Postfix, Dovecot, Rspamd, DKIM, антиспам, антивірус), веб-пошта з безпечним рендерингом HTML, кілька скриньок на користувача з перемикачем, окремі паролі застосунків для IMAP/SMTP-клієнтів |
| Редактор листів | Розмір шрифту, жирний / курсив / підкреслення / закреслення, списки, цитати, посилання (лише http(s)/mailto), вбудовані зображення (стають cid-вкладеннями), емоджі, підписи (кілька, один за замовчуванням), конфіденційний режим (див. нижче) |
| Чат | Особисті та групові розмови, доставка в реальному часі (WebSocket), індикатор набору тексту, редагування й видалення, непрочитані, вкладення файлів зі сховища |
| Адмінка | Користувачі, квоти, блокування, скидання 2FA, запрошення, статистика, журнал аудиту, поштові скриньки (створення, перейменування зі збереженням старої адреси як псевдоніма, ім'я відправника, квота), карантин і стан антивірусу, а також Django admin на прихованому шляху (лише з 2FA) |

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

Тести покривають: вхід і блокування, 2FA (зокрема повторне використання TOTP і одноразовість резервних кодів), запрошення, права адміністратора, атомарність квот, шифрування й цілісність чанків, ізоляцію файлів між користувачами, спільний доступ, кошик, публічні посилання (пароль, ліміт, підпис), санітизацію імен і HTML листів, календар і чат, карантин і антивірус (права, повторна перевірка, видалення з паролем), кілька скриньок і перейменування, підписи, HTML-листи з вбудованими зображеннями, конфіденційний режим.

Без Docker (потрібні Python 3.12, `libmagic`, `poppler-utils`):

```bash
cd backend && pip install -r requirements.txt
DJANGO_TEST=1 python -m pytest
cd ../frontend && npm install && npm run typecheck && npm run build
```

## Онлайн-редактор документів (ONLYOFFICE)

`make secrets` додає в `.env` рядки `OFFICE_ENABLED=true`, `COMPOSE_PROFILES=office` та випадковий `OFFICE_JWT_SECRET`. Після цього:

```bash
docker compose up -d --build
```

Document Server займає ~2–3 ГБ RAM і стартує 1–2 хвилини. Він доступний через Traefik на піддомені `office.DOMAIN`, бачить лише backend (мережа `edge`), а запити підписуються JWT. Щоб вимкнути редактор, постав `OFFICE_ENABLED=false` і прибери `office` з `COMPOSE_PROFILES`.

Мініатюри для файлів, завантажених до оновлення: `make thumbnails`.

## Змінні оточення

Значення задаються у `.env` (шаблон — `.env.example`) або у файлах `secrets/` (Docker secrets). **Реальні значення секретів не комітяться** і в цьому README не наводяться: `.env`, `secrets/*` і `certs/*` занесені в `.gitignore`, а `make secrets` генерує їх локально.

### `.env`

| Змінна | Призначення |
|---|---|
| `DOMAIN` | Домен веб-інтерфейсу (`localhost` для розробки) |
| `ACME_EMAIL` | Email для Let's Encrypt |
| `TLS_RESOLVER`, `SNI_STRICT` | Видача сертифікатів Traefik; для локальної розробки — порожньо / `false` |
| `ADMIN_URL_PREFIX` | Прихований шлях Django admin |
| `DJANGO_DEBUG` | Режим налагодження (дозволений лише з `DOMAIN=localhost`) |
| `TIME_ZONE`, `LANGUAGE_CODE` | Часовий пояс і мова сервера за замовчуванням |
| `REGISTRATION_OPEN` | Відкрита реєстрація (`false` — лише за запрошеннями) |
| `DEFAULT_QUOTA_GB`, `MAX_FILE_SIZE_GB` | Квота нового користувача і максимальний розмір файлу |
| `REQUIRE_2FA_FOR_STAFF` | Обов'язкова 2FA для адміністраторів |
| `UNSCANNED_POLICY` | Що робити з файлами, які ClamAV не може перевірити (`block` / `allow`) |
| `CLAMAV_ENABLED`, `CLAMAV_MAX_STREAM_MB` | Увімкнення антивірусу і ліміт перевірки (змінюється лише тут, не з вебінтерфейсу) |
| `OBJECT_STORE`, `S3_BUCKET`, `S3_ENDPOINT_URL` | Сховище файлів: `local` (Docker-том) або `s3` |
| `MAIL_DOMAIN`, `MAIL_HOSTNAME` | Поштовий домен і хост MX |
| `MAIL_AUTO_CREATE`, `MAIL_DEFAULT_QUOTA_MB` | Автоматична скринька для нового користувача та її квота |
| `SPAMHAUS_DQS_KEY` | Ключ Spamhaus DQS для антиспаму (секрет) |
| `OFFICE_ENABLED`, `OFFICE_HOST`, `OFFICE_JWT_SECRET` | Онлайн-редактор документів (ONLYOFFICE); JWT — секрет |
| `USERCONTENT_HOST` | Окремий origin для перегляду файлів |
| `COMPOSE_PROFILES` | Додаткові сервіси: `office`, `mailcert` |
| `*_IMAGE` | Закріплені за digest образи (`make pin`) |
| Додатково (є значення за замовчуванням) | `AUDIT_RETENTION_DAYS`, `TRASH_RETENTION_DAYS`, `PUBLIC_LINK_MAX_DAYS`, `SESSION_AGE_HOURS`, `LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_SECONDS`, `TWO_FACTOR_MAX_FAILURES`, `MAX_RECURRING_EVENTS_PER_USER`, `THUMBNAILS_ENABLED` |

### `secrets/` (генерує `make secrets`)

| Файл | Призначення |
|---|---|
| `django_secret_key` | Ключ підпису Django |
| `postgres_password`, `app_db_password`, `mail_db_password` | Паролі власника БД, обмеженої ролі застосунку і ролі пошти |
| `redis_password` | Пароль Redis |
| `file_master_keys` | Майстер-ключі шифрування файлів (втрата = втрата файлів) |
| `field_encryption_key` | Шифрування чутливих полів БД (TOTP, конфіденційні листи) |
| `dovecot_master_password` | Доступ веб-пошти до Dovecot |
| `mail_crypt_private_key`, `mail_crypt_public_key` | Шифрування листів на диску |
| `s3_access_key`, `s3_secret_key` | Лише для `OBJECT_STORE=s3` |

Для CI і автодеплою **жодних секретів у GitHub не потрібно** (див. нижче).

## CI (GitHub Actions)

`.github/workflows/ci.yml` запускається на push у `main`/`develop`, на pull request і вручну. Виконуються лише перевірки, налаштовані в репозиторії:

| Job | Кроки |
|---|---|
| Backend | Python 3.12 + `libmagic1`, `poppler-utils` → `pip install -r requirements.txt` → `manage.py check` → `makemigrations --check --dry-run` → `pytest` (налаштування з `backend/pytest.ini`, `DJANGO_TEST=1`: SQLite, без зовнішніх сервісів) |
| Frontend | Node 22 → `npm install --ignore-scripts` (або `npm ci`, якщо є lock-файл) → `npm run typecheck` → `npm run build` |
| Compose | Тимчасові секрети (`scripts/init-secrets.sh`) → `docker compose config` (основний і dev) → `sh -n` для всіх `*.sh` |

Лінтери (ruff, eslint) у проєкті не налаштовані, тому в CI їх немає.

## Автодеплой

`.github/workflows/deploy.yml` розгортає `main` після **успішного** CI для push у `main` (не для PR) або вручну (Actions → Deploy → Run workflow на гілці `main`).

Сервер стоїть за NAT, і SSH ззовні закритий, тому деплой виконує **self-hosted runner на самому сервері**: він сам підключається до GitHub, вхідні порти не потрібні. Workflow:

1. перевіряє, що в `/opt/blackcloud` немає локальних змін відстежуваних файлів (інакше зупиняється, нічого не перезаписуючи);
2. `git fetch` і fast-forward до **саме того коміту, що пройшов CI** (через наявний deploy key сервера);
3. `docker compose build` і `docker compose up -d` (міграції виконує сервіс `migrate` автоматично);
4. чекає, доки `backend` стане `healthy`, і перевіряє `https://DOMAIN/api/health/` через Traefik.

### Одноразове налаштування

1. **Сервер на гілку `main`:** `cd /opt/blackcloud && git status && git fetch origin && git checkout main && git pull --ff-only`.
2. **Runner:** GitHub → репозиторій → Settings → Actions → Runners → New self-hosted runner (Linux x64). Виконайте показані команди під користувачем, якому належить `/opt/blackcloud` і який є в групі `docker`, а під час `./config.sh` додайте мітку `blackcloud` (`--labels blackcloud`). Потім `sudo ./svc.sh install && sudo ./svc.sh start`, щоб runner працював як сервіс.
3. **(Рекомендовано) середовище `production`:** Settings → Environments → `production` → Deployment branches: лише `main`; за бажанням Required reviewers для ручного підтвердження кожного деплою.
4. **(Необов'язково) змінна репозиторію `DEPLOY_PATH`**, якщо проєкт лежить не в `/opt/blackcloud`.
5. Додаткові сервіси (`office`, `mailcert`) вмикаються через `COMPOSE_PROFILES` у `.env` на сервері — деплой їх враховує.

> Безпека runner-а: реєструйте його лише в цьому (приватному) репозиторії. Workflow для PR виконуються на хмарних runner-ах GitHub і ніколи — на сервері.

## Конфіденційний режим листів

Отримувачі одержують лише лист-повідомлення з посиланням `https://DOMAIN/c#токен`. Вміст зберігається на сервері зашифрованим (Fernet), у БД — лише SHA-256 токена. Можна задати термін (1, 7, 30 або 90 днів) і 6-значний код доступу (його треба передати іншим каналом). Відправник бачить список таких листів і може відкликати доступ: вміст одразу видаляється. Після терміну вміст стирає періодичне завдання.

Обмеження: вкладення в цьому режимі не підтримуються; знімок екрана чи копіювання тексту отримувачем запобігти неможливо (як і в Gmail).

## Мова інтерфейсу

Перемикач — у верхній панелі, на сторінці входу та в Налаштування → Профіль → Оформлення. Перекладено навігацію, вхід, налаштування профілю й оформлення, пошту (разом із редактором), нові вкладки адмінки та сторінку конфіденційного листа. Інші сторінки (файли, календар, чат, безпека) і повідомлення сервера поки лише українською. Рядки лежать у `frontend/src/i18n/uk.ts` та `en.ts`; TypeScript не дасть пропустити ключ у перекладі.

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
scripts/            init-secrets.sh, pin-images.sh
.github/workflows/  ci.yml (перевірки), deploy.yml (автодеплой)
```

Модель загроз і деталі захисту описані в [SECURITY.md](SECURITY.md).
