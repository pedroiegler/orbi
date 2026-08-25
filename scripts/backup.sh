#!/usr/bin/env bash
# ORBI — backup diario (ORBI.md secao 12).
#
# `pg_dump` diario + WAL archiving continuo, enviados para um provedor
# DIFERENTE do da aplicacao. Backup no mesmo fornecedor nao protege contra conta
# suspensa, incidente de faturamento ou falha regional.
#
# RPO 15 min · RTO 4h — declarados, nao implicitos.
#
#   BACKUP_REMOTE=b2:orbi-backups scripts/backup.sh
set -euo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="${BACKUP_WORK_DIR:-/var/backups/orbi}"
CONTAINER="${POSTGRES_CONTAINER:-orbi-app-postgres-1}"
DATABASE="${POSTGRES_DB:-orbi}"
REMOTE="${BACKUP_REMOTE:-}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

mkdir -p "$WORK_DIR"
DUMP="$WORK_DIR/orbi-$STAMP.dump"

echo "[orbi] gerando dump logico de $DATABASE"
docker exec "$CONTAINER" pg_dump -U postgres -d "$DATABASE" -Fc > "$DUMP"

echo "[orbi] verificando integridade do dump"
pg_restore --list "$DUMP" > /dev/null

if [ -n "$REMOTE" ]; then
  echo "[orbi] enviando para $REMOTE (provedor diferente do da aplicacao)"
  rclone copy "$DUMP" "$REMOTE/dumps/" --no-traverse
  rclone sync "${WAL_DIR:-/var/lib/docker/volumes/orbi-app_orbi-wal/_data}" "$REMOTE/wal/"
else
  echo "[orbi] AVISO: BACKUP_REMOTE nao definido — backup ficou na mesma maquina"
fi

find "$WORK_DIR" -name 'orbi-*.dump' -mtime "+$RETENTION_DAYS" -delete
echo "[orbi] backup concluido: $(basename "$DUMP")"
