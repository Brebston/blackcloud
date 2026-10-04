#!/bin/sh
set -eu
chown -R clamav:clamav /var/lib/clamav

# Перший запуск: бази потрібні до старту clamd (кілька хвилин, ~300 МБ)
if [ ! -s /var/lib/clamav/main.cvd ] && [ ! -s /var/lib/clamav/main.cld ]; then
  echo "Завантаження антивірусних баз (перший запуск)..."
  until freshclam --foreground --stdout; do
    echo "freshclam не вдався, повтор через 60 с"; sleep 60
  done
fi

# Оновлення баз у фоні (12 разів на добу)
freshclam --daemon --checks=12 --stdout &

exec clamd --foreground
