#!/bin/sh
# Ночная копия базы: сжатый дамп в томе backups, старше 14 дней удаляются.
set -eu
umask 077
name="/backups/umny-$(date +%Y-%m-%d-%H%M).sql.gz"
pg_dump -h db -U umny -d umny --no-owner | gzip > "$name.tmp"
mv "$name.tmp" "$name"
find /backups -name 'umny-*.sql.gz' -mtime +14 -delete
echo "Копия базы: $name"
