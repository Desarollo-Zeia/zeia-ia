#!/bin/bash
# Sincroniza una base local con una copia fresca de producción.
#
# Bases soportadas (módulos del producto):
#   energia   → PostgreSQL `energy`     (consumo eléctrico)
#   ambiental → PostgreSQL `valhalladb` (monitoreo ambiental)
#
# Pasos: 1) túnel SSH (reusa el abierto, si no lo abre y lo cierra al final)
#        2) pg_dump completo  3) restaura en la DB local  4) verifica conteos
#
# Uso:
#   scripts/sync_db.sh energia            # dump + restore + verificación
#   scripts/sync_db.sh ambiental
#   scripts/sync_db.sh energia --dump-only
#   scripts/sync_db.sh ambiental --restore-only DIR|FILE
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

BASE="${1:-}"
[ -z "$BASE" ] && { echo "uso: scripts/sync_db.sh <energia|ambiental> [--dump-only|--restore-only FILE]" >&2; exit 1; }
[ "$BASE" = "energia" ] || [ "$BASE" = "ambiental" ] || {
    echo "base desconocida: $BASE (usa 'energia' o 'ambiental')" >&2; exit 1
}
shift || true
MODE="${1:-full}"

# ---------- Configuración (pisar con variables de entorno si hace falta) ----------
set -a
source "$ROOT/.env"
set +a

PREFIX=$(echo "$BASE" | tr 'a-z' 'A-Z')

PG16_BIN="/opt/homebrew/opt/postgresql@16/bin"
PG_DUMP="${PG16_BIN}/pg_dump"
PG_RESTORE="${PG16_BIN}/pg_restore"
PSQL="${PG16_BIN}/psql"

TUNNEL_LOCAL_PORT="${TUNNEL_LOCAL_PORT:-${PREFIX}_DB_PORT}"
# Los nombres de las variables llevan prefijo: leer el valor real del .env
TUNNEL_LOCAL_PORT=$(eval echo "\${${PREFIX}_DB_PORT}")
LOCAL_PORT="${LOCAL_PORT:-5432}"
LOCAL_HOST="${LOCAL_HOST:-127.0.0.1}"
BK_DIR="${ROOT}/backups/${BASE}"
KEEP_DUMPS="${KEEP_DUMPS:-3}"
PARALLEL="${PARALLEL:-4}"

PROD_HOST="$(eval echo "\${SSH_HOST_${PREFIX}}")"
PROD_USER="$(eval echo "\${SSH_USER_${PREFIX}}")"
PROD_KEY="${ROOT}/$(eval echo "\${SSH_KEY_${PREFIX}}")"
PROD_REMOTE_HOST="$(eval echo "\${SSH_REMOTE_HOST_${PREFIX}}")"
PROD_REMOTE_PORT="$(eval echo "\${SSH_REMOTE_PORT_${PREFIX}}")"
DB_USER="$(eval echo "\${${PREFIX}_DB_USER}")"
DB_PASSWORD="$(eval echo "\${${PREFIX}_DB_PASSWORD}")"
DB_NAME="$(eval echo "\${${PREFIX}_DB_NAME}")"

H=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="${BK_DIR}/${BASE}_prod_${H}.dump"
TUNNEL_PID=""

log() { echo "[sync:$BASE] $*"; }

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
    log "dump completo de producción (paralelo -j ${PARALLEL}) -> ${DUMP_FILE} (esto tarda varios minutos)"
    PGPASSWORD="${DB_PASSWORD}" "${PG_DUMP}" -h 127.0.0.1 -p "${TUNNEL_LOCAL_PORT}" \
        -U "${DB_USER}" -d "${DB_NAME}" -Fd -j "${PARALLEL}" -Z 5 --verbose -f "${DUMP_FILE}"
    log "dump OK: $(du -sh "${DUMP_FILE}" | awk '{print $1}')"
    find "${DUMP_FILE}" -type f -exec shasum -a 256 {} + > "${DUMP_FILE}.sha256"
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
        -U "${DB_USER}" -d "${DB_NAME}" -j "${PARALLEL}" --no-owner --no-privileges "${SRC}"
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
    for f in "${BK_DIR}"/${BASE}_prod_*.dump; do
        [ -e "$f" ] || continue
        KEEP=$(ls -d "${BK_DIR}"/${BASE}_prod_*.dump | sort -r | head -n "${KEEP_DUMPS}" | grep -cF "$f" || true)
        [ "$KEEP" -eq 0 ] && { rm -rf "$f" "$f.sha256"; log "dump viejo eliminado: $(basename "$f")"; }
    done
}

trap close_tunnel EXIT

case "${MODE}" in
    --dump-only)
        open_tunnel
        dump_from_prod
        ;;
    --restore-only)
        [ $# -lt 1 ] && { log "uso: scripts/sync_db.sh ${BASE} --restore-only <archivo.dump>" >&2; exit 1; }
        SRC="$2"
        [ -f "${SRC}" ] || { log "ERROR: no existe ${SRC}" >&2; exit 1; }
        restore_local "${SRC}"
        ;;
    --help|-h)
        echo "uso: scripts/sync_db.sh <energia|ambiental> [--dump-only | --restore-only FILE]"
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
        echo "uso: scripts/sync_db.sh <energia|ambiental> [--dump-only | --restore-only FILE]" >&2
        exit 1
        ;;
esac

log "listo"