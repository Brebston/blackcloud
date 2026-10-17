<div align="center">

# ☁️ BlackCloud

**Self-hosted хмара з акцентом на безпеку: файли, фото, пошта, календар, чат і контакти на власному сервері.**

[![CI](https://github.com/Brebston/blackcloud/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Brebston/blackcloud/actions/workflows/ci.yml)
[![Deploy](https://github.com/Brebston/blackcloud/actions/workflows/deploy.yml/badge.svg?branch=main)](https://github.com/Brebston/blackcloud/actions/workflows/deploy.yml)

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![DRF](https://img.shields.io/badge/DRF-3.18-A30000?logo=django&logoColor=white)](https://www.django-rest-framework.org/)
[![Celery](https://img.shields.io/badge/Celery-5-37814A?logo=celery&logoColor=white)](https://docs.celeryq.dev/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.6-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Vite](https://img.shields.io/badge/Vite-5-646CFF?logo=vite&logoColor=white)](https://vitejs.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)](https://redis.io/)
<br>
[![Docker](https://img.shields.io/badge/Docker_Compose-v2-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Traefik](https://img.shields.io/badge/Traefik-v3-24A1C1?logo=traefikproxy&logoColor=white)](https://traefik.io/)
[![Let's Encrypt](https://img.shields.io/badge/Let's_Encrypt-TLS-003A70?logo=letsencrypt&logoColor=white)](https://letsencrypt.org/)
[![Postfix](https://img.shields.io/badge/Postfix%20·%20Dovecot%20·%20Rspamd-mail-4B5563)](https://www.postfix.org/)
[![ClamAV](https://img.shields.io/badge/ClamAV-antivirus-6B7280)](https://www.clamav.net/)
[![ONLYOFFICE](https://img.shields.io/badge/ONLYOFFICE-docs-FF6F3D?logo=onlyoffice&logoColor=white)](https://www.onlyoffice.com/)
[![Uptime Kuma](https://img.shields.io/badge/Uptime_Kuma-monitoring-5CDD8B?logo=uptimekuma&logoColor=white)](https://uptime.kuma.pet/)
[![restic](https://img.shields.io/badge/restic-backups-00ADD8)](https://restic.net/)
[![Hetzner](https://img.shields.io/badge/Hetzner-Storage_Box-D50C2D?logo=hetzner&logoColor=white)](https://www.hetzner.com/storage/storage-box/)
[![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-CI%2FCD-2088FF?logo=githubactions&logoColor=white)](https://github.com/features/actions)

[Можливості](#-можливості) ·
[Архітектура](#-архітектура) ·
[Встановлення на сервер](#-встановлення-на-сервер-з-нуля) ·
[Резервні копії](#крок-11-резервні-копії-в-hetzner-storage-box) ·
[Оновлення](#-оновлення) ·
[Розробка](#-локальна-розробка) ·
[Налаштування](#%EF%B8%8F-змінні-оточення) ·
[Безпека](SECURITY.md)

</div>

---

## Зміст

- [Можливості](#-можливості)
- [Технології](#-технології)
- [Архітектура](#-архітектура)
- [Вимоги](#-вимоги)
- [Встановлення на сервер з нуля](#-встановлення-на-сервер-з-нуля)
- [Оновлення](#-оновлення)
- [Локальна розробка](#-локальна-розробка)
- [Тести й CI/CD](#-тести-й-cicd)
- [Змінні оточення й секрети](#%EF%B8%8F-змінні-оточення)
- [Корисні команди](#-корисні-команди)
- [Документація модулів](#-документація-модулів)
- [Мова інтерфейсу](#-мова-інтерфейсу)
- [Структура репозиторію](#-структура-репозиторію)
- [Безпека й ліцензія](#-безпека-й-ліцензія)

---

## ✨ Можливості

| Модуль | Що вміє |
|---|---|
| **Акаунти** | Вхід за логіном або email, Argon2id, 2FA (TOTP + 10 одноразових резервних кодів), ключі доступу (passkeys: Face ID / Touch ID / Windows Hello / апаратні ключі — вхід без пароля або як другий фактор), блокування після 5 невдалих спроб, список пристроїв із віддаленим виходом, журнал подій безпеки, запрошення на email |
| **Файли** | Будь-які типи до 10 ГБ, чанкове завантаження з відновленням, шифрування AES-256-GCM на рівні застосунку, антивірус ClamAV і карантин, папки, пошук, кошик (30 днів), історія версій, ZIP теки, WebDAV (Finder, Windows, мобільні застосунки) |
| **Доступ** | Спільний доступ користувачам (читання), публічні посилання на файли й **теки** (пароль, термін, ZIP), «**скринька для файлів**» (гості лише надсилають файли в підтеку зі своїм ім'ям), **обране**, особисті кольорові **теги**, «**Нещодавні**» |
| **Перегляд** | Мініатюри фото, PDF і відео; повноекранний переглядач з масштабуванням і гортанням; HEIC/TIFF через велике зображення; відео з перемотуванням; Word/Excel/PowerPoint в ONLYOFFICE (кожне збереження — нова версія, перевірена антивірусом) |
| **Фото** | Стрічка фото й відео з усіх тек за датою зйомки (EXIF), групування за місяцями й днями, альбоми-посилання, камера/об'єктив/координати в панелі «Інформація»; у публічних посиланнях геолокація **завжди прибрана** |
| **Пошта** | Власний поштовий сервер (Postfix, Dovecot, Rspamd, DKIM, антиспам, антивірус), веб-пошта як у Gmail, кілька скриньок, редактор листів (TipTap), чернетки, відкладене надсилання, конфіденційний режим, адресна книга й підказки одержувачів з 2 літер, паролі застосунків для IMAP/SMTP |
| **Календар** | Кілька календарів, повторювані події (RRULE), нагадування, спільний доступ, імпорт/експорт .ics, приватна ICS-підписка |
| **Чат** | Особисті й групові розмови в реальному часі (WebSocket), набір тексту, редагування, вкладення зі сховища |
| **Адмінка** | Користувачі, квоти, блокування й видалення акаунтів (через 7 днів, можна скасувати), запрошення, журнал аудиту, поштові скриньки, карантин, **резервні копії** (стан і кнопка «Зробити копію зараз»), **«Стан сервера»** (диск, пам'ять, процесор, черги, сертифікати, служби) |
| **Застосунок** | PWA (встановлення на телефон і комп'ютер), Web Push, світла/темна тема, 8 акцентних кольорів, українська та англійська |

## 🧰 Технології

| Шар | Що використовується |
|---|---|
| Бекенд | Python 3.12, Django 5.2, Django REST Framework, Channels (WebSocket), Celery + beat |
| Фронтенд | React 18, TypeScript, Vite, TipTap, власні CSS-змінні (теми й акценти), PWA |
| Дані | PostgreSQL 16, Redis 7, зашифроване сховище файлів (Docker-том або S3) |
| Пошта | Postfix, Dovecot (mail_crypt), Rspamd (антиспам, DKIM), ClamAV |
| Інфраструктура | Traefik v3 (Let's Encrypt, заголовки безпеки, rate limit), Docker Compose, GitHub Actions + self-hosted runner |
| Експлуатація | Uptime Kuma + Telegram, restic → Hetzner Storage Box, Trivy, pip-audit / npm audit |

## 🏗 Архітектура

```
Інтернет ─► Traefik :80/:443 (TLS, HSTS, CSP, rate limit)
              ├─► frontend   (nginx, статичний React SPA)
              ├─► backend    (Django ASGI: REST API + WebSocket; usercontent.DOMAIN — лише перегляд файлів)
              ├─► dav        (той самий код, окремі процеси лише для /dav/)
              └─► onlyoffice (office.DOMAIN, окремий origin)
                              ├─ PostgreSQL  ─┐
                              ├─ Redis        │  мережа internal
                              ├─ том objects (шифротекст)  (без виходу
                              ├─ ClamAV       │   в інтернет)
                              ├─ Dovecot ◄────┤
                              └─ Postfix ◄────┘
             worker / beat (Celery): антивірус, мініатюри, EXIF, кошик, нагадування, видалення акаунтів
             worker ─► thumbnailer (мережа thumbs: без секретів і інтернету)
             pusher  ─► Web Push (єдиний сервіс з виходом в інтернет, без ключів шифрування)
             migrate (одноразово): міграції від власника БД, веб працює як обмежена роль bc_app
Інтернет ─► Postfix :25/465/587, Dovecot :993 (мережа mailnet)
Хост (systemd, root): restic → Hetzner Storage Box щоночі; Uptime Kuma на 127.0.0.1:3001
```

## 📋 Вимоги

| | Мінімум | Рекомендовано |
|---|---|---|
| ОС | Ubuntu 22.04 LTS | **Ubuntu 24.04 LTS** (x86_64) |
| CPU | 2 ядра | 4+ ядра |
| RAM | 4 ГБ (без ONLYOFFICE) | 8 ГБ (з ONLYOFFICE ~2–3 ГБ, ClamAV ~1,5 ГБ) |
| Диск | 40 ГБ + ваші дані | SSD для системи й БД, окремий великий диск для даних |
| Мережа | Публічна IP-адреса (статична), відкриті порти 80, 443, 25, 465, 587, 993 | Провайдер, що дозволяє вихідний порт 25 і налаштовує PTR |
| Домен | Свій домен із керуванням DNS | DNS у Cloudflare (режим **DNS only**) |
| Резервні копії | — | Hetzner Storage Box **BX11** (1 ТБ) |

> Домашній сервер за NAT підходить: потрібна статична публічна IP-адреса і переадресація портів на роутері. Автодеплой працює без вхідного SSH (self-hosted runner сам підключається до GitHub).

---

## 🚀 Встановлення на сервер з нуля

Нижче — повний шлях від чистого сервера до робочої хмари з поштою, моніторингом і резервними копіями. Приклад: домен `example.com`, веб-інтерфейс на `cloud.example.com`, пошта на `mail.example.com`, проєкт у `/opt/blackcloud`. Підставте свої значення.

### Крок 1. Підготовка системи

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git make curl ca-certificates gnupg ufw unattended-upgrades fail2ban
sudo timedatectl set-timezone Europe/Kyiv
sudo dpkg-reconfigure -plow unattended-upgrades   # автоматичні оновлення безпеки
```

Окремий користувач для проєкту (якщо працюєте від root):

```bash
sudo adduser brebston
sudo usermod -aG sudo brebston
```

**SSH лише за ключем.** На своєму комп'ютері: `ssh-copy-id brebston@<IP-сервера>`, перевірте вхід, потім на сервері в `/etc/ssh/sshd_config` встановіть `PasswordAuthentication no` і `PermitRootLogin no`, далі `sudo systemctl restart ssh`.

### Крок 2. Брандмауер і мережа

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp          # веб і Let's Encrypt
sudo ufw allow 25,465,587/tcp      # пошта: прийом і надсилання
sudo ufw allow 993/tcp             # IMAP для поштових клієнтів
sudo ufw enable
```

> Docker сам відкриває опубліковані порти в обхід `ufw`. У проєкті опубліковано лише перелічені вище порти, а Uptime Kuma прив'язана до `127.0.0.1`.

**Домашній сервер.** Закріпіть за сервером постійну LAN-адресу (DHCP reservation) і переадресуйте на неї на роутері TCP-порти **80, 443, 25, 465, 587, 993**. SSH назовні відкривати не потрібно.

**Порт 25.** Багато провайдерів і VPS блокують вихідний порт 25. Перевірте до встановлення:

```bash
timeout 7 bash -c '</dev/tcp/gmail-smtp-in.l.google.com/25' && echo "25 відкритий" || echo "25 ЗАБЛОКОВАНИЙ"
```

### Крок 3. Docker

Офіційний репозиторій Docker (Compose v2 входить у пакет):

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER      # потім вийдіть і зайдіть знову
docker compose version             # має бути v2.x
```

### Крок 4. DNS

Створіть записи в DNS-провайдера (у Cloudflare — **«DNS only», сіра хмаринка**: Let's Encrypt використовує TLS-ALPN-перевірку на порту 443, а пошта взагалі не проходить через проксі Cloudflare).

| Тип | Ім'я | Значення | Навіщо |
|---|---|---|---|
| A | `cloud.example.com` | IP сервера | веб-інтерфейс (`DOMAIN`) |
| A | `office.cloud.example.com` | IP сервера | редактор документів (окремий origin) |
| A | `usercontent.cloud.example.com` | IP сервера | перегляд файлів (окремий origin) |
| A | `mail.example.com` | IP сервера | поштовий сервер (`MAIL_HOSTNAME`) |
| MX | `example.com` | `10 mail.example.com` | куди доставляти пошту |
| TXT | `example.com` | `v=spf1 mx -all` | SPF |
| TXT | `mail.example.com` | `v=spf1 a -all` | SPF для HELO |
| TXT | `_dmarc.example.com` | `v=DMARC1; p=none; rua=mailto:dmarc@example.com; adkim=s; aspf=s` | DMARC (через тиждень — `p=quarantine`) |
| TXT | `mail._domainkey.example.com` | з `make dkim` (крок 8) | DKIM |
| A | `mta-sts.example.com` | IP сервера | MTA-STS (необов'язково, крок 8) |
| TXT | `_mta-sts.example.com`, `_smtp._tls.example.com` | з `make mta-sts` (крок 8) | MTA-STS і TLS-RPT (необов'язково) |
| PTR | IP сервера | `mail.example.com` | зворотний запис — **налаштовує провайдер** (напишіть у підтримку) |

Перевірка:

```bash
dig +short cloud.example.com
dig +short MX example.com
dig +short -x <IP-сервера>        # має бути mail.example.com.
```

### Крок 5. Код проєкту

Репозиторій приватний, тож потрібен **deploy key** (лише читання):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/blackcloud_deploy -N "" -C "blkbox deploy key"
cat ~/.ssh/blackcloud_deploy.pub
```

Додайте ключ у GitHub → репозиторій → Settings → Deploy keys → Add deploy key (без «Allow write access»). Потім:

```bash
cat >> ~/.ssh/config <<'CFG'
Host github.com
    IdentityFile ~/.ssh/blackcloud_deploy
    IdentitiesOnly yes
CFG
sudo mkdir -p /opt/blackcloud && sudo chown $USER:$USER /opt/blackcloud
git clone git@github.com:Brebston/blackcloud.git /opt/blackcloud
cd /opt/blackcloud && git checkout main
```

### Крок 6. Секрети й `.env`

```bash
cd /opt/blackcloud
make secrets       # створює secrets/* (ключі шифрування, паролі БД, Redis, Dovecot…) і .env з шаблону
nano .env
```

Обов'язково змініть у `.env`:

| Змінна | Приклад | Примітка |
|---|---|---|
| `DOMAIN` | `cloud.example.com` | домен веб-інтерфейсу |
| `ACME_EMAIL` | `admin@example.com` | для Let's Encrypt |
| `MAIL_DOMAIN` | `example.com` | адреси користувачів: `ім'я@example.com` |
| `MAIL_HOSTNAME` | `mail.example.com` | має збігатися з A, MX і PTR |
| `ADMIN_URL_PREFIX` | випадковий рядок | прихований шлях Django admin |
| `COMPOSE_PROFILES` | `office,mailcert,monitoring` | ONLYOFFICE, сертифікат пошти, Uptime Kuma |
| `SPAMHAUS_DQS_KEY` | ключ із spamhaus.com | безкоштовний (Data Query Service → Free) |

> **Збережіть копію каталогу `secrets/` поза сервером** (у менеджері паролів чи на зашифрованому носії). Без `secrets/file_master_keys` файли розшифрувати неможливо — це не відновлюється.

### Крок 7. Перший запуск

```bash
make lock && make pin   # (рекомендовано) залежності з хешами й образи, закріплені за digest
make init               # збірка, міграції (окремий сервіс migrate), запуск усіх сервісів
make superuser          # перший адміністратор
docker compose ps       # усі сервіси мають бути Up / healthy
```

Перший старт триває кілька хвилин: ClamAV завантажує бази, ONLYOFFICE стартує 1–2 хв, Let's Encrypt видає сертифікати.

Відкрийте `https://cloud.example.com`, увійдіть адміністратором і **одразу увімкніть 2FA** (Налаштування → Безпека): без неї адмін-функції заблоковані.

### Крок 8. Пошта

```bash
make dkim          # покаже TXT-запис mail._domainkey — додайте його в DNS (одним рядком, без лапок)
```

Перевірка (через 10–30 хв після змін DNS):

```bash
dig +short TXT mail._domainkey.example.com
```

Службові адреси (`postmaster@`, `abuse@`, `dmarc@`, `tlsrpt@` …) як псевдоніми на вашу скриньку:

```bash
docker compose exec backend python manage.py mail_service_aliases --to <ваш-логін>
```

**MTA-STS і TLS-RPT (рекомендовано):** обов'язковий TLS для вхідної пошти й звіти від Gmail/Outlook. Коротко: A-запис `mta-sts` → `MTA_STS_MODE=testing` у `.env` → деплой → TXT-записи з `make mta-sts`; через 2–4 тижні без помилок у звітах — `enforce`. Докладно — [docs/MAIL.md](docs/MAIL.md#mta-sts-і-tls-rpt).

Надішліть лист зі своєї скриньки на адресу з [mail-tester.com](https://www.mail-tester.com) — ціль 9–10/10 (SPF, DKIM, DMARC, PTR). Перевірте IP на [check.spamhaus.org](https://check.spamhaus.org). Новий домен перші 1–3 тижні може потрапляти в «Спам» у Gmail: це репутація, а не налаштування. Докладно — [docs/MAIL.md](docs/MAIL.md).

### Крок 9. Моніторинг (Uptime Kuma + Telegram)

Kuma слухає лише `127.0.0.1:3001`. З **вашого комп'ютера**:

```bash
ssh -L 3001:127.0.0.1:3001 brebston@<IP-сервера>   # потім http://localhost:3001
```

1. Створіть обліковий запис Kuma.
2. Settings → Notifications → **Telegram** (токен від @BotFather і ваш chat id) → Test.
3. Монітори: «Сайт» (HTTPS `https://cloud.example.com`), «Глибока перевірка» `…/api/health/deep/`, «Диск ≥ 85%» `…/api/health/deep/?only=disk` — з заголовком `{"Authorization": "Bearer <токен>"}`, токен: `sudo cat /opt/blackcloud/secrets/health_token`; TCP-монітори 25/465/587/993.

Покроково — [docs/MONITORING.md](docs/MONITORING.md).

### Крок 10. Автодеплой (необов'язково)

Після кожного зеленого CI на `main` сервер сам оновлюється.

1. GitHub → Settings → Actions → Runners → **New self-hosted runner** (Linux x64). Виконайте показані команди в `~/actions-runner` під користувачем, якому належить `/opt/blackcloud` (група `docker`). Під час `./config.sh` додайте мітку: `--labels blackcloud`.
2. Запустіть як сервіс: `sudo ./svc.sh install && sudo ./svc.sh start`.
3. (Рекомендовано) Settings → Environments → `production` → Deployment branches: лише `main`.
4. Якщо проєкт не в `/opt/blackcloud` — змінна репозиторію `DEPLOY_PATH`.

Деплой зупиняється, якщо в `/opt/blackcloud` є незакомічені зміни, і перевіряє `https://DOMAIN/api/health/` після старту.

### Крок 11. Резервні копії в Hetzner Storage Box

Щоночі (03:30) сервер робить **зашифровану інкрементальну** копію (restic, AES-256) усіх даних на Hetzner Storage Box, а щонеділі (05:00) перевіряє її пробним відновленням БД і випадкового файлу. Зберігаються 7 щоденних, 4 щотижневі й 12 щомісячних станів.

У копію потрапляють: дамп усіх баз PostgreSQL, файли користувачів (уже зашифровані), пошта (зашифрована mail_crypt), ключі DKIM, сертифікати, а також `secrets/` і `.env`.

**11.1. Storage Box.** Hetzner Console → Storage Box → замовте **BX11**. У налаштуваннях увімкніть **SSH support** і **External reachability**, задайте пароль. Запишіть логін `uXXXXX` і хост `uXXXXX.your-storagebox.de`. SMB і WebDAV Storage Box тримайте вимкненими.

**11.2. restic і скрипт:**

```bash
sudo apt install -y restic
restic version                   # потрібно 0.14+ (Ubuntu 24.04 — 0.16)
cd /opt/blackcloud
make backup-install              # скрипт → /usr/local/sbin/blackcloud-backup (власник root), таймери systemd
sudo blackcloud-backup init      # створить пароль restic і ПОКАЖЕ ЙОГО ОДИН РАЗ
```

> **Збережіть пароль restic у менеджері паролів** разом із логіном і паролем Storage Box. Без нього копії не розшифрувати — Hetzner його не має.

**11.3. Підключення до Storage Box.** `sudo nano /root/.ssh/config`:

```
Host storagebox
    HostName uXXXXX.your-storagebox.de
    User uXXXXX
    Port 23
    IdentityFile /etc/blackcloud-backup/id_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 60
```

Далі один зі способів входу:

- **За ключем (рекомендовано):**
  ```bash
  sudo sh -c 'cat /etc/blackcloud-backup/id_ed25519.pub | ssh -p 23 uXXXXX@uXXXXX.your-storagebox.de install-ssh-key'
  ```
- **За паролем Storage Box:** у блоці вище замініть `IdentityFile …` і `IdentitiesOnly yes` на `PubkeyAuthentication no`, потім
  ```bash
  sudo blackcloud-backup set-password    # пароль зберігається лише в /etc/blackcloud-backup (600, root)
  ```

Перше підключення й створення сховища:

```bash
sudo sftp storagebox             # підтвердіть відбиток: yes → ls → exit
sudo blackcloud-backup init      # створить сховище restic на Storage Box
```

**11.4. Сповіщення в Telegram.** В Uptime Kuma додайте два монітори типу **Push**:

| Монітор | Heartbeat Interval | Змінна в `/etc/blackcloud-backup/backup.env` |
|---|---|---|
| «Резервні копії» (щоночі) | `93600` (26 год) | `KUMA_PUSH_URL=http://127.0.0.1:3001/api/push/…` |
| «Перевірка копій» (щотижня) | `691200` (8 днів) | `KUMA_CHECK_PUSH_URL=http://127.0.0.1:3001/api/push/…` |

У Push URL замініть адресу на `http://127.0.0.1:3001`. Kuma повідомить і про невдалу копію, і про копію, яка взагалі не запустилася.

**11.5. Перша копія й розклад:**

```bash
make backup-now      # перша копія (кілька хвилин; надалі — секунди)
make backup-list     # список копій
make backup-check    # цілісність + пробне відновлення БД і файлу
make backup-enable   # щоночі о 03:30, перевірка щонеділі о 05:00
```

Стан копій і кнопка «Зробити копію зараз» — в **Адмінка → Резервні копії**. Відновлення й видалення копій з вебінтерфейсу свідомо неможливі: лише з терміналу сервера. Повне відновлення на новому сервері — [docs/BACKUP.md](docs/BACKUP.md#повне-відновлення-на-новому-сервері).

### Крок 12. Фінальна перевірка

- [ ] `https://cloud.example.com` відкривається з дійсним сертифікатом, адміністратор має 2FA
- [ ] Завантаження файлу → статус «Готовий» (антивірус працює), мініатюра з'являється
- [ ] Лист на Gmail: у «Показати оригінал» — `SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`
- [ ] Адмінка → Стан сервера: «Усе гаразд»
- [ ] Адмінка → Резервні копії: є свіжа копія, перевірка «Пройдено»
- [ ] Uptime Kuma: усі монітори зелені, тестове сповіщення прийшло в Telegram
- [ ] Копія `secrets/` і пароль restic збережені **поза** сервером

---

## 🔄 Оновлення

**З автодеплоєм:** злийте зміни в `main` — після зеленого CI сервер оновиться сам.

**Вручну:**

```bash
cd /opt/blackcloud
make backup-now                    # перед міграціями БД — обов'язково
git pull --ff-only
sh scripts/init-secrets.sh --secrets-only   # додасть нові секрети, наявні не змінює
docker compose build && docker compose up -d   # міграції виконає сервіс migrate
docker compose ps
```

Після оновлення скрипта копій: `make backup-install`.

## 💻 Локальна розробка

```bash
make secrets
# у .env: DOMAIN=localhost, TLS_RESOLVER= (порожньо), SNI_STRICT=false, DJANGO_DEBUG=true
# /etc/hosts (Safari/Firefox; Chrome резолвить *.localhost сам):
#   127.0.0.1 office.localhost usercontent.localhost
make up-dev             # https://localhost
```

**Довірений сертифікат для localhost (рекомендовано)** — без нього браузер не реєструє service worker, а Finder не підключає WebDAV:

```bash
brew install mkcert nss
mkcert -install
mkdir -p certs/dev
mkcert -cert-file certs/dev/localhost.pem -key-file certs/dev/localhost-key.pem \
  localhost office.localhost usercontent.localhost 127.0.0.1 ::1
make up-dev
```

Фронтенд з hot-reload: `cd frontend && npm install && npm run dev` (API проксіюється на `https://localhost`).

Після зміни моделей: `make migrations` (закомітьте створені міграції).

## 🧪 Тести й CI/CD

```bash
make test                                   # тести бекенду в Docker
```

Без Docker (Python 3.12, `libmagic`, `poppler-utils`, за бажанням `ffmpeg`):

```bash
cd backend && pip install -r requirements.txt
DJANGO_TEST=1 python -m pytest
python scripts/i18n.py check
cd ../frontend && npm install && npm run typecheck && npm run build
```

| Workflow | Коли | Що робить |
|---|---|---|
| `ci.yml` | push у `main`/`develop`, pull request, вручну | **Backend:** `manage.py check`, `makemigrations --check`, `i18n.py check`, `pytest`. **Frontend:** `npm run typecheck`, `npm run build`. **Compose:** `docker compose config`, `sh -n` для скриптів |
| `deploy.yml` | після зеленого CI на `main`, вручну | self-hosted runner на сервері: fast-forward до перевіреного коміту → `docker compose build` → `up -d` → перевірка здоров'я |

Для CI й автодеплою **жодних секретів у GitHub не потрібно**. Workflow для PR виконуються лише на хмарних runner-ах GitHub, ніколи — на сервері.

Ланцюг постачання: `make lock` (залежності з хешами), `make pin` (образи за digest), `make audit` (pip-audit, npm audit), `make scan` (Trivy).

## ⚙️ Змінні оточення

Значення — у `.env` (шаблон `.env.example`) і у файлах `secrets/` (Docker secrets). **Реальні значення не комітяться**: `.env`, `secrets/*` і `certs/*` — у `.gitignore`.

<details>
<summary><b><code>.env</code> — усі змінні</b></summary>

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
| `REQUIRE_2FA_FOR_STAFF` | Обов'язкова 2FA (або ключ доступу) для адміністраторів |
| `UNSCANNED_POLICY` | Файли, які ClamAV не може перевірити: `block` / `allow` |
| `CLAMAV_ENABLED`, `CLAMAV_MAX_STREAM_MB` | Антивірус і ліміт перевірки |
| `OBJECT_STORE`, `S3_BUCKET`, `S3_ENDPOINT_URL` | Сховище файлів: `local` (Docker-том) або `s3` |
| `MAIL_DOMAIN`, `MAIL_HOSTNAME` | Поштовий домен і хост MX |
| `MAIL_AUTO_CREATE`, `MAIL_DEFAULT_QUOTA_MB` | Автоматична скринька для нового користувача та її квота |
| `SYSTEM_MAIL_FROM`, `SITE_NAME` | Відправник системних листів (запрошення) і назва сервісу в них |
| `SPAMHAUS_DQS_KEY` | Ключ Spamhaus DQS для антиспаму |
| `MTA_STS_MODE`, `MTA_STS_MX`, `MTA_STS_MAX_AGE`, `TLSRPT_RUA` | MTA-STS і TLS-RPT (порожній режим — вимкнено) |
| `OFFICE_ENABLED`, `OFFICE_HOST`, `OFFICE_JWT_SECRET` | ONLYOFFICE; JWT — секрет |
| `USERCONTENT_HOST` | Окремий origin для перегляду файлів |
| `COMPOSE_PROFILES` | Додаткові сервіси: `office`, `mailcert`, `monitoring` |
| `WEB_WORKERS`, `DAV_WORKERS` | Кількість процесів веб-інтерфейсу й WebDAV |
| `HEALTH_DISK_ALERT_PERCENT`, `HEALTH_CERT_MIN_DAYS`, `BACKUP_MAX_AGE_HOURS` | Пороги для моніторингу |
| `ACCOUNT_DELETION_DELAY_DAYS`, `ACCOUNT_NAME_HOLD_DAYS` | Відкладене видалення акаунта (7) і утримання імені/адреси (30) |
| `*_IMAGE` | Закріплені за digest образи (`make pin`) |
| Додатково | `AUDIT_RETENTION_DAYS`, `TRASH_RETENTION_DAYS`, `PUBLIC_LINK_MAX_DAYS`, `SESSION_AGE_HOURS`, `LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_SECONDS`, `TWO_FACTOR_MAX_FAILURES`, `THUMBNAILS_ENABLED` |

</details>

<details>
<summary><b><code>secrets/</code> — файли, які створює <code>make secrets</code></b></summary>

| Файл | Призначення |
|---|---|
| `django_secret_key` | Ключ підпису Django |
| `postgres_password`, `app_db_password`, `mail_db_password` | Власник БД, обмежена роль застосунку, роль пошти |
| `redis_password` | Пароль Redis |
| `file_master_keys` | **Майстер-ключі шифрування файлів (втрата = втрата файлів)** |
| `field_encryption_key` | Шифрування чутливих полів БД (TOTP, координати фото, конфіденційні листи) |
| `dovecot_master_password` | Доступ веб-пошти до Dovecot |
| `mail_crypt_private_key`, `mail_crypt_public_key` | Шифрування листів на диску |
| `health_token` | Токен для `/api/health/deep/` (моніторинг) |
| `vapid_private_key`, `vapid_public_key` | Web Push |
| `s3_access_key`, `s3_secret_key` | Лише для `OBJECT_STORE=s3` |

Налаштування резервних копій (пароль restic, ключ SSH, Push URL Kuma) лежать окремо — у `/etc/blackcloud-backup` (700, root).

</details>

## 🛠 Корисні команди

| Команда | Дія |
|---|---|
| `make init` / `make up` / `make down` | перший запуск / запуск / зупинка |
| `make logs` | логи всіх сервісів |
| `make superuser` / `make shell` | адміністратор / Django shell |
| `make migrate` / `make migrations` | застосувати / створити міграції |
| `make dkim` | DKIM-запис для DNS |
| `make mta-sts` | DNS-записи й політика MTA-STS / TLS-RPT |
| `make thumbnails` | мініатюри для старих файлів |
| `docker compose exec backend python manage.py index_photos` | додати всі фото в «Фото» одразу |
| `make backup-install` / `backup-now` / `backup-check` / `backup-list` / `backup-enable` | резервні копії restic → Hetzner |
| `make backup` | локальний дамп PostgreSQL у `backups/` (не замінює копії поза сервером) |
| `make lock` / `make pin` / `make audit` / `make scan` | ланцюг постачання |

## 📚 Документація модулів

| Документ | Про що |
|---|---|
| [docs/BACKUP.md](docs/BACKUP.md) | Резервні копії, що в них, повне відновлення на новому сервері |
| [docs/MONITORING.md](docs/MONITORING.md) | Uptime Kuma, Telegram, монітори, `/api/health/deep/` |
| [docs/FILES.md](docs/FILES.md) | Версії файлів, ZIP, публічні теки, скринька, обране й теги |
| [docs/PHOTOS.md](docs/PHOTOS.md) | Галерея «Фото», EXIF, альбоми, прибирання геолокації |
| [docs/MAIL.md](docs/MAIL.md) | Веб-пошта, групові дії, заблоковані відправники, MTA-STS і TLS-RPT |
| [docs/WEBDAV.md](docs/WEBDAV.md) | Мережевий диск у Finder, Windows, Linux, на телефоні |
| [docs/PUSH.md](docs/PUSH.md) | PWA і Web Push |
| [docs/BIMI.md](docs/BIMI.md) | Логотип у Gmail (BIMI) |
| [SECURITY.md](SECURITY.md) | Модель загроз і захист |

## 🌐 Мова інтерфейсу

Українська й англійська — повністю, включно з повідомленнями сервера. Перемикач — у верхній панелі, на сторінці входу і в налаштуваннях.

- **Фронтенд:** `frontend/src/i18n/uk.ts`, `en.ts` і `parts/*.ts`; TypeScript не дає пропустити ключ.
- **Бекенд:** рядки в коді українською з `gettext`, переклад — `backend/locale/en/LC_MESSAGES/django.po`:
  ```bash
  cd backend
  python scripts/i18n.py extract   # нові рядки → .po
  python scripts/i18n.py compile   # .po → .mo
  python scripts/i18n.py check     # те саме перевіряє CI
  ```

## 📁 Структура репозиторію

```
backend/            Django: apps/{core,accounts,storage,photos,calendars,chat,mail,push,dav}, tests/, thumbsvc.py
frontend/           React SPA (Vite + TypeScript), nginx.conf
mailserver/         postfix/, dovecot/, rspamd/, clamav/, postgres-init/
traefik/dynamic/    маршрути, заголовки безпеки, TLS
scripts/            init-secrets.sh, pin-images.sh, backup.sh, systemd/
docs/               документація модулів
.github/workflows/  ci.yml (перевірки), deploy.yml (автодеплой)
```

## 🔒 Безпека й ліцензія

- Модель загроз і деталі захисту — [SECURITY.md](SECURITY.md). Знайшли вразливість — повідомте власника репозиторію напряму, не через публічні issue.
- Секрети ніколи не комітяться; для CI/CD вони не потрібні.
- Приватний проєкт. Ліцензію не визначено — усі права застережено.
