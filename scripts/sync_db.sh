#!/bin/bash
# Sincroniza la DB local con una copia fresca de producción.
# Pasos: 1) túnel SSH (reusa el abierto, si no lo abre y lo cierra al final)
#        2) pg_dump completo  3) restaura en la DB local  4) verifica conteos
# Uso:   scripts/sync_db.sh             # dump + restore + verificación
#        scripts/sync_db.sh --dump-only # solo descargar el dump
#        scripts/sync_db.sh --restore-only DIR|FILE  # solo restaurar un dump
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# ---------- Configuración (pisar con variables de entorno si hace falta) ----------
set -a
source "$ROOT/.env"
set +a

PG16_BIN="/opt/homebrew/opt/postgresql@16/bin"
PG_DUMP="${PG16_BIN}/pg_dump"
PG_RESTORE="${PG16_BIN}/pg_restore"
PSQL="${PG16_BIN}/psql"

TUNNEL_LOCAL_PORT="${TUNNEL_LOCAL_PORT:-55432}"
LOCAL_PORT="${LOCAL_PORT:-5432}"
LOCAL_HOST="${LOCAL_HOST:-127.0.0.1}"
BK_DIR="${ROOT}/backups"
KEEP_DUMPS="${KEEP_DUMPS:-3}"

PROD_HOST="${SSH_HOST}"
PROD_USER="${SSH_USER}"
PROD_KEY="${ROOT}/${SSH_KEY}"
PROD_REMOTE_HOST="${SSH_REMOTE_HOST}"
PROD_REMOTE_PORT="${SSH_REMOTE_PORT}"

H=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="${BK_DIR}/energy_prod_${H}.dump"
TUNNEL_PID=""

log() { echo "[sync] $*"; }

tunnel_up() { nc -z 127.0.0.1 "$TUNNEL_LOCAL_PORT" >/dev/null 2>&1; }

open_tunnel() {
    mkdir -p "${BK_DIR}"
    if tunnel_up; then
        log "túnel ya abierto en 127.0.0.1:${TUNNEL_LOCAL_PORT} (reusando)"
        return
    fi
    log "abriendo túnel SSH hacia ${PROD_HOST} (puerto ${TUNNEL_LOCAL_PORT})..."
    ssh -i "${PROD_KEY}" -N -L "${TUNNEL_LOCAL_PORT}:${PROD_REMOTE_HOST}:${PROD_REMOTE_PORT}" \
        "${PROD_USER}@${PROD_HOST}" >"${BK_DIR}/tunnel_sync.log" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 15); do
        sleep 1
        tunnel_up && { log "túnel listo (pid ${TUNNEL_PID})"; return; }
    done
    log "ERROR: no se pudo abrir el túnel. Ver ${BK_DIR}/tunnel_sync.log" >&2
    exit 1
}

close_tunnel() {
    if [ -n "${TUNNEL_PID}" ]; then
        kill "${TUNNEL_PID}" 2>/dev/null || true
        log "túnel cerrado (solo el que abrió este script)"
    fi
}

dump_from_prod() {
    mkdir -p "${BK_DIR}"
    log "dump completo de producción -> ${DUMP_FILE} (esto tarda varios minutos)"
    PGPASSWORD="${DB_PASSWORD}" "${PG_DUMP}" -h 127.0.0.1 -p "${TUNNEL_LOCAL_PORT}" \
        -U "${DB_USER}" -d "${DB_NAME}" -Fc --verbose -f "${DUMP_FILE}"
    log "dump OK: $(ls -lh "${DUMP_FILE}" | awk '{print $5}')"
    shasum -a 256 "${DUMP_FILE}" > "${DUMP_FILE}.sha256"
}

restore_local() {
    local SRC="$1"
    log "verificando servidor local en ${LOCAL_HOST}:${LOCAL_PORT}..."
    if ! nc -z "${LOCAL_HOST}" "${LOCAL_PORT}" >/dev/null 2>&1; then
        log "ERROR: servidor local caído. Inicia con: brew services start postgresql@16" >&2
        exit 1
    fi
    log "borrando esquemas actuales de la DB local..."
    PGPASSWORD="${DB_PASSWORD}" "${PSQL}" -h "${LOCAL_HOST}" -p "${LOCAL_PORT}" \
        -U "${DB_USER}" -d "${DB_NAME}" \
        -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
    log "restaurando ${SRC} en la DB local (varios minutos)..."
    PGPASSWORD="${DB_PASSWORD}" "${PG_RESTORE}" -h "${LOCAL_HOST}" -p "${LOCAL_PORT}" \
        -U "${DB_USER}" -d "${DB_NAME}" --no-owner --no-privileges "${SRC}"
    log "restore terminado"
}

verify() {
    local Q="SELECT count(*) FROM ${1:-readings_reading};"
    local PROD_N LOCAL_N
    PROD_N=$(PGPASSWORD="${DB_PASSWORD}" "${PSQL}" -h 127.0.0.1 -p "${TUNNEL_LOCAL_PORT}" \
        -U "${DB_USER}" -d "${DB_NAME}" -t -A -c "${Q}" 2>/dev/null || echo "?")
    LOCAL_N=$(PGPASSWORD="${DB_PASSWORD}" "${PSQL}" -h "${LOCAL_HOST}" -p "${LOCAL_PORT}" \
        -U "${DB_USER}" -d "${DB_NAME}" -t -A -c "${Q}" || echo "?")
    log "lecturas  prod=${PROD_N}  local=${LOCAL_N}  $([ "${PROD_N}" = "${LOCAL_N}" ] && echo 'OK' || echo 'DIFERENTES!')"
    Q="SELECT max(created_at) FROM readings_reading;"
    log "última lectura prod: $(PGPASSWORD="${DB_PASSWORD}" "${PSQL}" -h 127.0.0.1 -p "${TUNNEL_LOCAL_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -t -A -c "${Q}" 2>/dev/null || echo '?')"
    log "última lectura local: $(PGPASSWORD="${DB_PASSWORD}" "${PSQL}" -h "${LOCAL_HOST}" -p "${LOCAL_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -t -A -c "${Q}")"
}

cleanup_dumps() {
    local N KEEP
    for f in "${BK_DIR}"/energy_prod_*.dump; do
        [ -e "$f" ] || continue
        KEEP=$(ls "${BK_DIR}"/energy_prod_*.dump | sort -r | head -n "${KEEP_DUMPS}" | grep -cF "$f" || true)
        [ "$KEEP" -eq 0 ] && { rm -f "$f" "$f.sha256"; log "dump viejo eliminado: $(basename "$f")"; }
    done
}

trap close_tunnel EXIT

MODE="${1:-full}"

case "${MODE}" in
    --dump-only)
        open_tunnel
        dump_from_prod
        ;;
    --restore-only)
        [ $# -lt 2 ] && { log "uso: scripts/sync_db.sh --restore-only <archivo.dump>" >&2; exit 1; }
        SRC="$2"
        [ -f "${SRC}" ] || { log "ERROR: no existe ${SRC}" >&2; exit 1; }
        restore_local "${SRC}"
        ;;
    --help|-h)
        echo "uso: scripts/sync_db.sh [--dump-only | --restore-only FILE]"
        exit 0
        ;;
    full)
        open_tunnel
        dump_from_prod
        restore_local "${DUMP_FILE}"
        verify
        cleanup_dumps
        ;;
    *)
        echo "uso: scripts/sync_db.sh [--dump-only | --restore-only FILE]" >&2
        exit 1
        ;;
esac

log "listo"