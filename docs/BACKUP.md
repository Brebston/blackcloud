## Резервні копії: restic → Hetzner Storage Box

Щоночі сервер робить зашифровану копію всіх даних на Hetzner Storage Box (поза домом), щонеділі
перевіряє її пробним відновленням і повідомляє в Telegram (через Uptime Kuma), якщо щось не так.

### Що потрапляє в копію

| Що | Звідки | Навіщо |
|---|---|---|
| Усі бази PostgreSQL (`pg_dumpall --clean`) | `/var/backups/blackcloud/db.sql` | користувачі, метадані файлів, пошта, календар, чат |
| Файли користувачів | том `objects_data` | уже зашифровані AES-256-GCM |
| Пошта | том `mail_data` | зашифрована Dovecot mail_crypt |
| Ключі DKIM, антиспам | том `rspamd_data` | без DKIM-ключа доведеться міняти DNS |
| Сертифікати | том `traefik_acme` | щоб не випускати заново |
| Секрети й налаштування | `/opt/blackcloud/secrets`, `.env` | **ключі шифрування файлів і пошти** |

Не копіюється (відтворюється само): бази ClamAV, Redis (кеш і черга), статика, ONLYOFFICE, образи Docker.

**Розмір і зберігання.** Копії інкрементальні й дедупліковані: щоночі передається лише нове.
Зберігаються 7 щоденних, 4 щотижневі й 12 щомісячних станів (`KEEP_*` у налаштуваннях) — усе разом
займає ~1,2–2 обсяги даних. Копія шифрується restic (AES-256) паролем, якого немає в Hetzner.

**Безпека.** Скрипт виконується від root (лише root читає томи Docker), тому він копіюється в
`/usr/local/sbin/blackcloud-backup` з власником root (`make backup-install`): root ніколи не
виконує файл із робочої копії, яку оновлює автодеплой. Налаштування, пароль і ключ SSH лежать
лише в `/etc/blackcloud-backup` (700, root), не в git.

### Налаштування (один раз)

**1. Storage Box.** У Hetzner Console замовте Storage Box **BX11** (1 ТБ). У налаштуваннях
увімкніть **SSH support** і **External reachability**, задайте пароль. Запишіть логін `uXXXXX`
і адресу `uXXXXX.your-storagebox.de`.

**2. restic на сервері:**
```sh
sudo apt install restic
restic version          # потрібно 0.14+ (Ubuntu 24.04 — 0.16)
```
Якщо версія старша (Ubuntu 22.04 — 0.12), встановіть офіційну збірку з
https://github.com/restic/restic/releases (`restic_*_linux_amd64.bz2` → `bunzip2` → `/usr/local/bin/restic`).

**3. Скрипт і ключі** (у `/opt/blackcloud`, після деплою цієї версії):
```sh
make backup-install
sudo blackcloud-backup init
```
`init` створить пароль restic і **покаже його один раз** — збережіть у менеджері паролів
разом із логіном і паролем Storage Box. Без цього пароля копії не розшифрувати.

Додайте в `/root/.ssh/config` (`sudo nano /root/.ssh/config`), підставивши свій `uXXXXX`:
```
Host storagebox
    HostName uXXXXX.your-storagebox.de
    User uXXXXX
    Port 23
    IdentityFile /etc/blackcloud-backup/id_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 60
```
Далі — **один із двох способів входу**:

*а) За ключем (рекомендовано).* Вставте вміст `sudo cat /etc/blackcloud-backup/id_ed25519.pub`
у Hetzner Console → Storage Box → SSH keys (або: `sudo sh -c 'cat /etc/blackcloud-backup/id_ed25519.pub | ssh -p 23 uXXXXX@uXXXXX.your-storagebox.de install-ssh-key'`).

*б) За паролем Storage Box.* У блоці `Host storagebox` замініть рядки `IdentityFile …` і
`IdentitiesOnly yes` на `PubkeyAuthentication no` і збережіть пароль на сервері:
```sh
sudo blackcloud-backup set-password    # введення не відображається; файл 600, лише root
```
restic отримує пароль через `SSH_ASKPASS` (сам скрипт віддає його ssh), тож пароль не видно
ні в аргументах процесів, ні в журналах. Тримайте вимкненими SMB і WebDAV Storage Box:
пароль діє й для них. Змінили пароль у Hetzner — повторіть `set-password`.

Перше підключення (підтвердіть відбиток Storage Box: `yes`; за паролем — введіть його вручну), потім створіть сховище:
```sh
sudo sftp storagebox                   # ls → exit
sudo blackcloud-backup init            # створить сховище restic
```

**4. Сповіщення в Telegram.** В Uptime Kuma (`ssh -L 3001:127.0.0.1:3001 brebston@blkbox`,
http://localhost:3001) додайте монітор **Push** «Резервна копія», Heartbeat Interval
**93600** с (26 год), сповіщення — ваш Telegram. Скопіюйте Push URL, замініть у ньому
`https://…` на `http://127.0.0.1:3001` і впишіть у `/etc/blackcloud-backup/backup.env`:
```
KUMA_PUSH_URL=http://127.0.0.1:3001/api/push/XXXXXXXXXX
```
За бажанням — другий монітор «Перевірка копій» (інтервал 8 днів = 691200 с) → `KUMA_CHECK_PUSH_URL`.
Kuma повідомить, і якщо копія впала, і якщо вона взагалі не запускалась (сервер вимкнений, таймер зупинено).

**5. Перша копія, перевірка, розклад:**
```sh
make backup-now        # перша копія (кілька хвилин; надалі — секунди)
make backup-list       # список копій
make backup-check      # перевірка цілісності + пробне відновлення БД і файлу
make backup-enable     # щоночі о 03:30, перевірка щонеділі о 05:00
```

### Адмінка → «Резервні копії»

Вкладка показує стан останньої копії й перевірки (з текстом помилки, якщо щось не так), наступний
запуск, розмір сховища, політику зберігання та історію копій, і має кнопку **«Зробити копію зараз»**.

Як це влаштовано (застосунок не отримує доступу до копій):
- скрипт після кожного запуску пише `backup-state/status/status.json` (без секретів); у backend
  цей каталог змонтований **лише для читання**;
- кнопка створює файл-запит `backup-state/request/run` — більше backend нічого не може;
  `blackcloud-backup-request.path` (systemd) бачить файл і запускає звичайну копію від root;
  запит видаляється на початку копії; повторний запит, поки копія триває, — 409; не частіше 6/год;
- відновлення й видалення копій в адмінці немає свідомо — лише з терміналу сервера;
- `/api/health/deep/` має перевірку `backup` (остання вдала копія давніша за 26 год → `fail`,
  поріг — `BACKUP_MAX_AGE_HOURS`); до налаштування копій перевірка пропускається.

Каталоги створює `make backup-install`; після `make backup-enable` стан з'являється одразу
(`sudo blackcloud-backup status` оновлює його вручну).

### Щоденне користування

- Журнал: `sudo journalctl -u blackcloud-backup -n 50`, перевірки — `-u blackcloud-backup-check`.
- Таймери: `systemctl list-timers 'blackcloud-backup*'`.
- Після оновлення скрипта в репозиторії: `make backup-install` (копіює нову версію).
- Випадково видалений файл чи стара версія — спершу **кошик** і **історія версій** у вебінтерфейсі.
  Резервні копії — для аварій (диск, сервер, пожежа).

### Повне відновлення на новому сервері

1. Ubuntu + Docker + restic + git; `git clone` репозиторію в `/opt/blackcloud` (гілка `main`).
2. Відновіть доступ до копій: `sudo install -d -m 700 /etc/blackcloud-backup`, впишіть пароль
   restic з менеджера паролів у `/etc/blackcloud-backup/restic_password`, виконайте
   `make backup-install && sudo blackcloud-backup init` і крок 3 налаштування (новий ключ SSH).
3. Розпакуйте останню копію:
   ```sh
   sudo blackcloud-backup snapshots
   sudo blackcloud-backup restic restore latest --target /srv/restore
   ```
4. Секрети й налаштування:
   ```sh
   sudo cp -a /srv/restore/opt/blackcloud/secrets/. /opt/blackcloud/secrets/
   sudo cp -a /srv/restore/opt/blackcloud/.env /opt/blackcloud/.env
   ```
5. Томи з даними:
   ```sh
   cd /opt/blackcloud && docker compose build && docker compose create
   for v in objects_data mail_data rspamd_data traefik_acme; do
     sudo cp -a /srv/restore/var/lib/docker/volumes/blackcloud_$v/_data/. \
       "$(docker volume inspect -f '{{ .Mountpoint }}' blackcloud_$v)/"
   done
   ```
6. База даних (у свіжий PostgreSQL; повідомлення «role … already exists» — очікувані):
   ```sh
   docker compose up -d postgres
   docker compose exec -T postgres psql -q -U blackcloud -d postgres < /srv/restore/var/backups/blackcloud/db.sql
   ```
7. `docker compose up -d`, перевірте вхід, файли й пошту. Якщо змінилась IP-адреса — оновіть
   DNS і переадресацію портів на роутері. Потім `sudo rm -rf /srv/restore` і `make backup-enable`.

Раз на рік варто прогнати це відновлення на тестовій віртуальній машині — щотижнева перевірка
підтверджує, що копія читається й БД відновлюється, але не замінює повної «пожежної» репетиції.
