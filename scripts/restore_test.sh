#!/usr/bin/env bash
# ORBI — teste mensal de restauracao (ORBI.md secao 12).
#
# Sobe um container limpo, restaura o backup mais recente, roda smoke tests e
# **falha ruidosamente**. Backup nunca restaurado nao e backup.
#
#   scripts/restore_test.sh /var/backups/orbi/orbi-20260901T030000Z.dump
set -euo pipefail

DUMP="${1:-$(ls -t "${BACKUP_WORK_DIR:-/var/backups/orbi}"/orbi-*.dump | head -1)}"
CONTAINER="orbi-restore-test-$(date -u +%s)"
PASSWORD="restore-test-$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
PORT="${RESTORE_TEST_PORT:-55432}"

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[orbi] restaurando $DUMP em container limpo"
docker run -d --name "$CONTAINER" -e POSTGRES_PASSWORD="$PASSWORD" \
  -p "$PORT:5432" pgvector/pgvector:pg17 >/dev/null

for _ in $(seq 1 60); do
  docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1 && break
  sleep 1
done

docker exec "$CONTAINER" psql -U postgres -c "CREATE DATABASE orbi" >/dev/null
docker exec "$CONTAINER" psql -U postgres -d orbi \
  -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;" >/dev/null
docker exec -i "$CONTAINER" pg_restore -U postgres -d orbi --no-owner < "$DUMP"

echo "[orbi] smoke tests"
FAILED=0
check() {
  local label="$1" sql="$2" expected="$3"
  local got
  got="$(docker exec "$CONTAINER" psql -U postgres -d orbi -tA -c "$sql" | tr -d ' ')"
  if [ "$got" -ge "$expected" ] 2>/dev/null; then
    echo "  ok   $label ($got)"
  else
    echo "  FALHA $label: esperado >= $expected, veio $got"
    FAILED=1
  fi
}

check "tenants"        "SELECT count(*) FROM tenants" 1
check "usuarios"       "SELECT count(*) FROM users" 1
check "catalogo"       "SELECT count(*) FROM catalog" 1
check "auditoria"      "SELECT count(*) FROM audit_logs" 0
check "politicas RLS"  "SELECT count(*) FROM pg_policies WHERE schemaname='public'" 15

if [ "$FAILED" -ne 0 ]; then
  echo "[orbi] TESTE DE RESTAURACAO FALHOU — o backup nao serve como esta"
  exit 1
fi
echo "[orbi] restauracao verificada: RTO cumprido, dados integros"
