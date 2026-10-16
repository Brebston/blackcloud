#!/usr/bin/env bash
# Резервні копії BlackCloud: restic → Hetzner Storage Box (SFTP, порт 23).
#
# Встановлюється як /usr/local/sbin/blackcloud-backup (make backup-install) і запускається
# від root таймерами systemd. Скрипт у репозиторії root НЕ виконує: його може змінити
# автодеплой або будь-хто з правами на /opt/blackcloud — це був би шлях до root.
#
#   blackcloud-backup init       — ключ SSH, пароль restic, шаблон налаштувань, ініціалізація сховища
#   blackcloud-backup set-password — зберегти пароль Storage Box (якщо вхід за паролем, а не ключем)
#   blackcloud-backup run        — нічна копія (БД + файли + пошта + ключі) і прибирання старих
#   blackcloud-backup check      — щотижнева перевірка: цілісність + пробне відновлення БД і файлу
#   blackcloud-backup snapshots  — список копій
#   blackcloud-backup status     — оновити стан для адмінки (список копій, розмір, розклад)
#   blackcloud-backup restic …   — будь-яка команда restic з налаштованим сховищем
#
# Адмінка BlackCloud бачить лише <BLACKCLOUD_DIR>/backup-state/status/status.json (без секретів)
# і може створити файл-запит backup-state/request/run — тоді systemd (blackcloud-backup-request.path)
# запускає звичайну копію. Веб-застосунок нічого не виконує на хості й не має доступу до сховища.
#
# Налаштування й секрети — лише на сервері, у /etc/blackcloud-backup (700, root), не в git:
#   backup.env       RESTIC_REPOSITORY, KUMA_PUSH_URL, політика зберігання
#   restic_password  пароль шифрування копій (збережіть копію ПОЗА сервером!)
#   id_ed25519       ключ SSH для Storage Box (або storagebox_password — вхід за паролем)
#   storagebox_password  пароль Storage Box, якщо вхід за паролем (600, лише root)
set -Eeuo pipefail
umask 077

CONF_DIR=/etc/blackcloud-backup

# Режим SSH_ASKPASS: ssh (його запускає restic) питає пароль Storage Box — віддаємо з файлу.
# Так пароль не потрапляє ні в аргументи процесів, ні в журнали, і не потрібен sshpass.
if [ "${BC_SSH_ASKPASS:-}" = 1 ]; then
  cat "$CONF_DIR/storagebox_password"
  exit 0
fi
CONF="$CONF_DIR/backup.env"
DUMP_DIR=/var/backups/blackcloud
LOCK=/run/blackcloud-backup.lock

log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ПОМИЛКА: $*"; exit 1; }

load_conf() {
  [ "$(id -u)" -eq 0 ] || die "запускайте від root (sudo)"
  [ -f "$CONF" ] || die "немає $CONF — спершу: sudo blackcloud-backup init"
  # shellcheck disable=SC1090
  . "$CONF"
  : "${RESTIC_REPOSITORY:?не задано RESTIC_REPOSITORY у $CONF}"
  export RESTIC_REPOSITORY
  export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-$CONF_DIR/restic_password}"
  export RESTIC_CACHE_DIR=/var/cache/restic
  if [ -s "$CONF_DIR/storagebox_password" ]; then
    # Вхід за паролем: OpenSSH 8.4+ бере пароль у SSH_ASKPASS навіть без терміналу
    export SSH_ASKPASS SSH_ASKPASS_REQUIRE=force BC_SSH_ASKPASS=1
    SSH_ASKPASS=$(readlink -f "$0")
  fi
  BLACKCLOUD_DIR="${BLACKCLOUD_DIR:-/opt/blackcloud}"
  COMPOSE_PROJECT="${COMPOSE_PROJECT:-blackcloud}"
  KEEP_DAILY="${KEEP_DAILY:-7}"
  KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
  KEEP_MONTHLY="${KEEP_MONTHLY:-12}"
  command -v restic >/dev/null || die "restic не встановлено: sudo apt install restic"
  STATE_DIR="$BLACKCLOUD_DIR/backup-state"
  STATUS_FILE="$STATE_DIR/status/status.json"
  REQUEST_FILE="$STATE_DIR/request/run"
  prepare_state_dirs
}

# backup-state/status — читає backend (uid 10001, лише читання); request — туди він пише запит
prepare_state_dirs() {
  install -d -m 755 -o root -g root "$STATE_DIR" "$STATE_DIR/status"
  install -d -m 700 -o 10001 -g 10001 "$STATE_DIR/request"
}

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Стан для адмінки (JSON без секретів). status_set СЕКЦІЯ ключ значення … | status_refresh
status_py() {
  python3 - "$STATUS_FILE" "$@" <<'PY'
import datetime, json, os, re, subprocess, sys

path, action, *args = sys.argv[1:]
try:
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        data = {}
except (OSError, ValueError):
    data = {}


def iso(value):
    # restic: 2026-10-14T03:30:12.123456789+02:00 → без дробових секунд (зручно для браузера)
    return re.sub(r"\.\d+", "", value) if isinstance(value, str) else value


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return None


if action == "set":
    section, *kv = args
    obj = {}
    for key, value in zip(kv[::2], kv[1::2]):
        if key.endswith(("_s", "_bytes")) and value.lstrip("-").isdigit():
            value = int(value)
        obj[key] = value
    data[section] = obj
    if obj.get("status") == "ok" and section == "last_backup":
        data["last_success_at"] = obj.get("finished_at")
    if obj.get("status") == "ok" and section == "last_check":
        data["last_check_ok_at"] = obj.get("finished_at")
elif action == "refresh":
    r = run(["restic", "snapshots", "--host", "blackcloud", "--json"])
    if r is not None and r.returncode == 0:
        snaps = sorted(json.loads(r.stdout or "[]"), key=lambda s: s.get("time", ""), reverse=True)
        data["snapshot_count"] = len(snaps)
        data["snapshots"] = [
            {
                "id": s.get("short_id") or str(s.get("id", ""))[:8],
                "time": iso(s.get("time")),
                "data_added": (s.get("summary") or {}).get("data_added"),
                "files_new": (s.get("summary") or {}).get("files_new"),
                "files_changed": (s.get("summary") or {}).get("files_changed"),
            }
            for s in snaps[:60]
        ]
    r = run(["restic", "stats", "--mode", "raw-data", "--json"])
    if r is not None and r.returncode == 0:
        try:
            data["repo_size_bytes"] = json.loads(r.stdout).get("total_size")
        except ValueError:
            pass
    schedule = {}
    for key, unit in (("next_backup", "blackcloud-backup.timer"), ("next_check", "blackcloud-backup-check.timer")):
        r = run(["systemctl", "show", unit, "--property=NextElapseUSecRealtime", "--value"])
        value = r.stdout.strip() if r is not None and r.returncode == 0 else ""
        schedule[key] = None
        if value in ("", "n/a", "0", "infinity"):
            # Поки таймер щойно запустив службу (а стан пишеться саме під час копії), systemd
            # ще не порахував наступний запуск — рахуємо з розкладу таймера (OnCalendar).
            value = ""
            c = run(["systemctl", "show", unit, "--property=TimersCalendar", "--value"])
            m = re.search(r"OnCalendar=([^;}]+)", c.stdout if c is not None else "")
            if m:
                a = run(["systemd-analyze", "calendar", m.group(1).strip()])
                n = re.search(r"Next elapse:\s*(.+)", a.stdout if a is not None and a.returncode == 0 else "")
                value = n.group(1).strip() if n else ""
        if value:
            d = run(["date", "-u", "-d", value, "+%Y-%m-%dT%H:%M:%SZ"])
            schedule[key] = d.stdout.strip() if d is not None and d.returncode == 0 else None
    data["schedule"] = schedule
    data["retention"] = {k.lower(): int(os.environ.get(k, "0") or 0) for k in ("KEEP_DAILY", "KEEP_WEEKLY", "KEEP_MONTHLY")}

data["version"] = 1
data["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(data, fh, ensure_ascii=False)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
}
status_set() { status_py set "$@" || log "не вдалося оновити стан для адмінки"; }
status_refresh() { KEEP_DAILY="$KEEP_DAILY" KEEP_WEEKLY="$KEEP_WEEKLY" KEEP_MONTHLY="$KEEP_MONTHLY" status_py refresh || log "не вдалося оновити стан для адмінки"; }

# Останні рядки журналу запуску — коротке пояснення помилки для адмінки (паролів restic не друкує)
error_tail() { tail -n 6 "$RUN_LOG" 2>/dev/null | tr -s '\n' ' ' | cut -c1-600; }

# Сповіщення через Uptime Kuma (монітор типу Push): немає сигналу понад добу або status=down → Telegram
notify() {
  local status="$1" msg="$2" url="${3:-${KUMA_PUSH_URL:-}}"
  [ -n "$url" ] || return 0
  curl -fsS -m 15 -G "$url" --data-urlencode "status=$status" --data-urlencode "msg=$msg" -o /dev/null || true
}

compose() { (cd "$BLACKCLOUD_DIR" && docker compose "$@"); }

volume_path() {
  docker volume inspect -f '{{ .Mountpoint }}' "${COMPOSE_PROJECT}_$1" 2>/dev/null \
    || die "том ${COMPOSE_PROJECT}_$1 не знайдено"
}

with_lock() {
  exec 9>"$LOCK"
  flock -n 9 || die "інша копія/перевірка вже виконується"
}

# ─────────────────────────── init ───────────────────────────

cmd_init() {
  [ "$(id -u)" -eq 0 ] || die "запускайте від root (sudo)"
  command -v restic >/dev/null || die "restic не встановлено: sudo apt install restic"
  install -d -m 700 "$CONF_DIR" "$DUMP_DIR" /var/cache/restic

  if [ ! -s "$CONF_DIR/restic_password" ]; then
    head -c 48 /dev/urandom | base64 | tr -d '\n=+/' | head -c 48 > "$CONF_DIR/restic_password"
    chmod 600 "$CONF_DIR/restic_password"
    log "Створено пароль restic: $CONF_DIR/restic_password"
    echo
    echo "  ⚠  Збережіть цей пароль у менеджері паролів (без нього копії не розшифрувати):"
    echo "     $(cat "$CONF_DIR/restic_password")"
    echo
  fi

  if [ ! -s "$CONF_DIR/id_ed25519" ]; then
    ssh-keygen -q -t ed25519 -N "" -C "blackcloud-backup@$(hostname)" -f "$CONF_DIR/id_ed25519"
    log "Створено ключ SSH: $CONF_DIR/id_ed25519"
  fi

  if [ ! -f "$CONF" ]; then
    cat > "$CONF" <<'EOF'
# Налаштування резервних копій BlackCloud (лише на сервері, не в git)
# Сховище: псевдонім "storagebox" з /root/.ssh/config (див. docs/BACKUP.md), тека blackcloud
RESTIC_REPOSITORY=sftp:storagebox:blackcloud
RESTIC_PASSWORD_FILE=/etc/blackcloud-backup/restic_password
BLACKCLOUD_DIR=/opt/blackcloud
COMPOSE_PROJECT=blackcloud
# Скільки копій зберігати
KEEP_DAILY=7
KEEP_WEEKLY=4
KEEP_MONTHLY=12
# Uptime Kuma → монітор «Push» (Heartbeat Interval 26 год): сповіщення в Telegram, якщо копії немає
# KUMA_PUSH_URL=http://127.0.0.1:3001/api/push/XXXXXXXXXX
# Окремий монітор для щотижневої перевірки (Heartbeat Interval 8 днів), необов'язково
# KUMA_CHECK_PUSH_URL=http://127.0.0.1:3001/api/push/YYYYYYYYYY
EOF
    chmod 600 "$CONF"
    log "Створено шаблон налаштувань: $CONF"
  fi

  if ! grep -qs "^Host storagebox$" /root/.ssh/config; then
    echo
    echo "Далі (див. docs/BACKUP.md, крок 3):"
    echo "  1) Додайте в /root/.ssh/config блок «Host storagebox» з вашим uXXXXX."
    echo "  2) Вхід за ключем: вставте $CONF_DIR/id_ed25519.pub у Storage Box (Hetzner Console → SSH keys)"
    echo "     або вхід за паролем: sudo blackcloud-backup set-password"
    echo "  3) Перше підключення (підтвердити відбиток): sudo sftp storagebox"
    echo "  4) Повторіть: sudo blackcloud-backup init"
    return 0
  fi

  load_conf
  if restic cat config >/dev/null 2>&1; then
    log "Сховище вже ініціалізоване: $RESTIC_REPOSITORY"
  else
    restic init
    log "Сховище створено: $RESTIC_REPOSITORY"
  fi
}

# ─────────────────────────── run ───────────────────────────

cmd_run() {
  load_conf
  # Запит з адмінки («зробити копію зараз») прибираємо одразу, щоб systemd не запускав повторно
  TRIGGER=schedule
  REQUESTED_BY=""
  if [ -f "$REQUEST_FILE" ]; then
    TRIGGER=manual
    REQUESTED_BY=$(head -c 300 "$REQUEST_FILE" | python3 -c 'import json,sys
try: print(str(json.load(sys.stdin).get("user",""))[:64])
except Exception: print("")' 2>/dev/null || true)
    rm -f "$REQUEST_FILE"
  fi
  with_lock
  RUN_LOG=$(mktemp /var/tmp/bc-backup-log.XXXXXX)
  exec > >(tee -a "$RUN_LOG") 2>&1
  STARTED_ISO=$(now_iso)
  STARTED=$(date +%s)
  status_set last_backup status running started_at "$STARTED_ISO" trigger "$TRIGGER" requested_by "$REQUESTED_BY"
  # Будь-яке завершення з помилкою (і через die) → стан «failed» в адмінці + сповіщення
  trap 'rc=$?
        if [ "$rc" -ne 0 ]; then
          status_set last_backup status failed started_at "$STARTED_ISO" finished_at "$(now_iso)" \
            duration_s "$(( $(date +%s) - STARTED ))" trigger "$TRIGGER" requested_by "$REQUESTED_BY" message "$(error_tail)"
          notify down "Резервна копія НЕ вдалась (код $rc). journalctl -u blackcloud-backup"
        fi
        rm -f "$RUN_LOG"' EXIT
  install -d -m 700 "$DUMP_DIR"

  # 1. Узгоджений дамп усіх баз (і ролей) без зупинки сайту. --clean: при відновленні
  #    у свіжий кластер наявні БД перестворюються з дампу.
  log "Дамп PostgreSQL…"
  compose exec -T postgres pg_dumpall -U blackcloud --clean --if-exists > "$DUMP_DIR/db.sql.tmp"
  [ -s "$DUMP_DIR/db.sql.tmp" ] || die "порожній дамп БД"
  mv -f "$DUMP_DIR/db.sql.tmp" "$DUMP_DIR/db.sql"

  # 2. Що зберігаємо. Не зберігаємо те, що відтворюється: бази ClamAV, Redis (кеш і черга),
  #    статика, кеш ONLYOFFICE, образи Docker.
  local paths=(
    "$DUMP_DIR"
    "$(volume_path objects_data)"   # файли (вже зашифровані AES-256-GCM)
    "$(volume_path mail_data)"      # пошта (зашифрована mail_crypt)
    "$(volume_path rspamd_data)"    # ключі DKIM, статистика антиспаму
    "$(volume_path traefik_acme)"   # сертифікати Let's Encrypt
    "$BLACKCLOUD_DIR/secrets"       # ключі шифрування файлів і пошти, паролі сервісів
    "$BLACKCLOUD_DIR/.env"
  )
  log "restic backup…"
  restic backup --tag nightly --host blackcloud --one-file-system \
    --exclude "$(volume_path objects_data)/.tmp" "${paths[@]}"

  log "Прибирання старих копій (${KEEP_DAILY}д/${KEEP_WEEKLY}т/${KEEP_MONTHLY}м)…"
  restic forget --tag nightly --host blackcloud --prune \
    --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" --keep-monthly "$KEEP_MONTHLY"

  local secs=$(( $(date +%s) - STARTED ))
  log "Готово за ${secs} с"
  status_set last_backup status ok started_at "$STARTED_ISO" finished_at "$(now_iso)" duration_s "$secs" \
    trigger "$TRIGGER" requested_by "$REQUESTED_BY" message ""
  status_refresh
  local total; total=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("repo_size_bytes") or 0)' "$STATUS_FILE" 2>/dev/null || echo 0)
  notify up "OK за ${secs} с, сховище $(( ${total:-0} / 1024 / 1024 )) МБ"
}

# ─────────────────────────── check ───────────────────────────

cmd_check() {
  load_conf
  with_lock
  push="${KUMA_CHECK_PUSH_URL:-${KUMA_PUSH_URL:-}}"
  # Глобальні (не local): їх використовує trap EXIT, що спрацьовує вже після виходу з функції
  tmp=$(mktemp -d /var/tmp/bc-restore-test.XXXXXX)
  pg="bc-restore-test-$$"
  RUN_LOG="$tmp/run.log"
  exec > >(tee -a "$RUN_LOG") 2>&1
  STARTED_ISO=$(now_iso)
  STARTED=$(date +%s)
  status_set last_check status running started_at "$STARTED_ISO"
  # Прибирання, стан для адмінки і сповіщення при будь-якому завершенні з помилкою
  trap 'rc=$?
        if [ "$rc" -ne 0 ]; then
          status_set last_check status failed started_at "$STARTED_ISO" finished_at "$(now_iso)" \
            duration_s "$(( $(date +%s) - STARTED ))" message "$(error_tail)"
          notify down "Перевірка копій НЕ пройшла (код $rc). journalctl -u blackcloud-backup-check" "$push"
        fi
        docker rm -f "$pg" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT

  # 1. Цілісність сховища: структура + 10 % даних читаються й розшифровуються
  log "restic check (10 % даних)…"
  restic check --read-data-subset=10%

  # 2. Пробне відновлення БД з останньої копії в тимчасовий PostgreSQL без мережі
  log "Пробне відновлення БД…"
  restic restore latest --host blackcloud --target "$tmp" --include "$DUMP_DIR/db.sql"
  local image; image=$(cd "$BLACKCLOUD_DIR" && sed -n 's/^POSTGRES_IMAGE=//p' .env | tail -n1)
  docker run -d --name "$pg" --network none -e POSTGRES_PASSWORD="$(head -c 18 /dev/urandom | base64)" \
    "${image:-postgres:16-alpine}" >/dev/null
  for _ in $(seq 1 60); do
    docker exec "$pg" pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 1
  done
  # Помилки на кшталт «роль postgres уже існує» очікувані; важливий результат нижче
  docker exec -i "$pg" psql -q -U postgres -d postgres >/dev/null 2>"$tmp/psql.err" < "$tmp$DUMP_DIR/db.sql" || true
  local users files
  users=$(docker exec "$pg" psql -tA -U postgres -d blackcloud -c "SELECT count(*) FROM accounts_user")
  files=$(docker exec "$pg" psql -tA -U postgres -d blackcloud -c "SELECT count(*) FROM storage_file")
  [ "${users:-0}" -ge 1 ] || die "у відновленій БД немає користувачів"
  log "БД відновлюється: користувачів $users, файлів $files"

  # 3. Випадковий файл зі сховища відновлюється байт-у-байт (якщо з часу копії не змінювався)
  local objects; objects=$(volume_path objects_data)
  local sample; sample=$(find "$objects" -type f -path '*/files/*' -mmin +1500 2>/dev/null | shuf -n1 || true)
  if [ -n "$sample" ]; then
    restic restore latest --host blackcloud --target "$tmp/f" --include "$sample"
    cmp -s "$sample" "$tmp/f$sample" || die "файл $sample у копії відрізняється від оригіналу"
    log "Файл відновлюється без змін: ${sample#"$objects"/}"
  fi

  status_set last_check status ok started_at "$STARTED_ISO" finished_at "$(now_iso)" \
    duration_s "$(( $(date +%s) - STARTED ))" message "Користувачів у БД: $users, файлів: $files"
  status_refresh
  notify up "Перевірка OK: користувачів $users, файлів $files" "$push"
}

cmd_set_password() {
  [ "$(id -u)" -eq 0 ] || die "запускайте від root (sudo)"
  install -d -m 700 "$CONF_DIR"
  local pw
  read -r -s -p "Пароль Storage Box (не відображається): " pw
  echo
  [ -n "$pw" ] || die "порожній пароль"
  printf '%s\n' "$pw" > "$CONF_DIR/storagebox_password"
  chmod 600 "$CONF_DIR/storagebox_password"
  log "Збережено: $CONF_DIR/storagebox_password (600, root)"
}

case "${1:-}" in
  init) cmd_init ;;
  set-password) cmd_set_password ;;
  run) cmd_run ;;
  check) cmd_check ;;
  snapshots) load_conf; restic snapshots --host blackcloud ;;
  status) load_conf; status_refresh; log "Стан оновлено: $STATUS_FILE" ;;
  restic) load_conf; shift; restic "$@" ;;
  *) sed -n '2,20p' "$0"; exit 2 ;;
esac
