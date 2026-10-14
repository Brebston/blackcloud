#!/usr/bin/env bash
# Резервні копії BlackCloud: restic → Hetzner Storage Box (SFTP, порт 23).
#
# Встановлюється як /usr/local/sbin/blackcloud-backup (make backup-install) і запускається
# від root таймерами systemd. Скрипт у репозиторії root НЕ виконує: його може змінити
# автодеплой або будь-хто з правами на /opt/blackcloud — це був би шлях до root.
#
#   blackcloud-backup init       — ключ SSH, пароль restic, шаблон налаштувань, ініціалізація сховища
#   blackcloud-backup run        — нічна копія (БД + файли + пошта + ключі) і прибирання старих
#   blackcloud-backup check      — щотижнева перевірка: цілісність + пробне відновлення БД і файлу
#   blackcloud-backup snapshots  — список копій
#   blackcloud-backup restic …   — будь-яка команда restic з налаштованим сховищем
#
# Налаштування й секрети — лише на сервері, у /etc/blackcloud-backup (700, root), не в git:
#   backup.env       RESTIC_REPOSITORY, KUMA_PUSH_URL, політика зберігання
#   restic_password  пароль шифрування копій (збережіть копію ПОЗА сервером!)
#   id_ed25519       ключ SSH для Storage Box
set -Eeuo pipefail
umask 077

CONF_DIR=/etc/blackcloud-backup
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
  BLACKCLOUD_DIR="${BLACKCLOUD_DIR:-/opt/blackcloud}"
  COMPOSE_PROJECT="${COMPOSE_PROJECT:-blackcloud}"
  KEEP_DAILY="${KEEP_DAILY:-7}"
  KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
  KEEP_MONTHLY="${KEEP_MONTHLY:-12}"
  command -v restic >/dev/null || die "restic не встановлено: sudo apt install restic"
}

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
    echo "  2) Встановіть публічний ключ у Storage Box:"
    echo "     sudo sh -c 'cat $CONF_DIR/id_ed25519.pub | ssh -p 23 uXXXXX@uXXXXX.your-storagebox.de install-ssh-key'"
    echo "  3) Повторіть: sudo blackcloud-backup init"
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
  with_lock
  # Будь-яке завершення з помилкою (і через die) → сповіщення
  trap 'rc=$?; [ "$rc" -eq 0 ] || notify down "Резервна копія НЕ вдалась (код $rc). journalctl -u blackcloud-backup"' EXIT
  local started; started=$(date +%s)
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

  local secs=$(( $(date +%s) - started ))
  local total; total=$(restic stats latest --mode raw-data --json 2>/dev/null | sed -n 's/.*"total_size":\([0-9]*\).*/\1/p')
  log "Готово за ${secs} с"
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
  # Прибирання і сповіщення при будь-якому завершенні з помилкою
  trap 'rc=$?; docker rm -f "$pg" >/dev/null 2>&1 || true; rm -rf "$tmp"
        [ "$rc" -eq 0 ] || notify down "Перевірка копій НЕ пройшла (код $rc). journalctl -u blackcloud-backup-check" "$push"' EXIT

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

  notify up "Перевірка OK: користувачів $users, файлів $files" "$push"
}

case "${1:-}" in
  init) cmd_init ;;
  run) cmd_run ;;
  check) cmd_check ;;
  snapshots) load_conf; restic snapshots --host blackcloud ;;
  restic) load_conf; shift; restic "$@" ;;
  *) sed -n '2,20p' "$0"; exit 2 ;;
esac
