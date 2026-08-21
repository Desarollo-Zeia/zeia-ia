# ZEIA — Asistente de IA para clientes (multi-base)

Documento de especificaciones y guía de arranque del producto: un asistente
conversacional (agente IA) que lee las bases de datos de los clientes y
responde cualquier pregunta sobre su información, complementando los módulos
actuales de monitoreo (energía y ambiental).

---

## 1. Visión del producto

- **Hoy**: entregamos módulos por cliente (backend + frontend por base de
  datos). El cliente navega varias pestañas para analizar su información; el
  análisis profundo resulta engorroso.
- **Con IA**: un agente dedicado que entiende la estructura de las bases del
  cliente y responde en lenguaje natural cualquier pregunta (consumos,
  cortes, alertas, calidad de aire, tendencias, anomalías), con análisis más
  detallado que el módulo actual, tablas y gráficos.
- **Diferenciador**: el agente tiene el mapa completo del esquema (no
  explora a ciegas), conoce el negocio (unidades, umbrales, tarifas) y solo
  lee datos (nunca modifica).

## 2. Arquitectura

```
┌──────────────┐   /api/chat (base=energia|ambiental)
│  webapp.py   │──────────────────────────────►  src/agent.py (EnergyAgent)
│  (FastAPI)   │                                    │ loop OpenRouter + tools
│  :8000       │◄───────────────────────────────────┘
└──────┬───────┘
       │ src/config.py (2 configs: ENERGIA_DB, AMBIENTAL_DB)
       ▼
src/db.py (engines separados, read-only, timeout, validación SELECT-only)
src/tunnel.py (túnel SSH por base: energía y ambiental)
```

- **Un agente por base** (módulo): `EnergyAgent(base="energia")` usa el
  prompt de energía; `EnergyAgent(base="ambiental")` usa el prompt ambiental.
- **Web**: una sola app con selector de módulo en el header (⚡ Energía /
  🌡 Ambiental). Sesiones separadas por base.
- **CLI**: `python cli.py --base ambiental`.

### Archivos clave

| Archivo | Rol |
|---|---|
| `src/config.py` | `DBConfig` por base + alias retrocompatibles (`DB_*` = energía) |
| `src/db.py` | `get_engine(base)`, `run_query(sql, base)`, introspección con `base` |
| `src/tunnel.py` | `ensure_tunnel(base)` — un túnel por módulo |
| `src/agent.py` | `EnergyAgent(base=...)`, inyecta fecha de hoy (Lima) al prompt |
| `src/prompts.py` | `SYSTEM_PROMPT` (energía) y `SYSTEM_PROMPT_AMBIENTAL` |
| `src/tools.py` | tools SQL parametrizadas con `base` |
| `scripts/sync_db.sh` | sincronización `energia` o `ambiental` |
| `webapp.py` / `web/static/index.html` | API + UI con selector de módulo |

## 3. Bases de datos (dos, independientes)

Ambas viven en el **mismo servidor PostgreSQL remoto** `172.31.29.136:5432`
(dos bastiones SSH distintos), pero en la máquina de trabajo corren **en
local** en `127.0.0.1` con puertos distintos:

| Módulo | Base | Nombre DB | Puerto local (trabajo) | Bastión SSH (casa) | Clave |
|---|---|---|---|---|---|
| Energía | PostgreSQL | `energy` | **5432** | `54.242.41.196` | `energy.pem` |
| Ambiental | PostgreSQL | `valhalladb` | **5433** | `ec2-44-206-41-101...` | `valhallaprod.pem` |

> ⚠️ `energy` está restringida por pg_hba desde el host del bastión ambiental:
> cada base se accede con SU clave y SU bastión. No intercambiar.

### 3.1 Módulo Energía (base `energy`)

- ~9.3M lecturas, 1 lectura/min por punto de medición (dic-2025 → hoy).
- Jerarquía: `enterprises_enterprise` → `enterprises_energyheadquarter`
  (sedes) → `enterprises_electricalpanel` (tableros) → `devices_device` →
  `enterprises_measurementpoint` (circuitos).
- Columnas case-sensitive (`"EPpos_value"` contador kWh, `"P_value"` W...).
- Empresas reales: Sanna, BanBif, Oechsle, Pizza Hut, BK, KFC, Madam Tusan.
- `created_at` UTC → siempre `AT TIME ZONE 'America/Lima'`.

### 3.2 Módulo Ambiental (base `valhalladb`)

- ~15.6M lecturas en `readings_reading`, **1 lectura cada ~5 min** por
  indicador (CO2, HUMIDITY, TEMPERATURE reportan juntas; ~288/día/indicador).
- Jerarquía: `enterprise_enterprise` → `enterprise_headquarters` →
  `enterprise_room` (salas) → `equipments_device` (sensores AM 103 / AM 107 /
  TS-201-TH) → `equipments_indicatordevice` → `readings_reading`.
- `value` es TEXT → `NULLIF(value,'')::numeric`.
- **Cliente con datos activos: Sanna, San Borja** (13 salas: Sala técnica,
  Sala de tomografía, Subestación, Sala UPS, Salas de Operaciones 1–5,
  Ducto resonador, Zonas Verde/Azul/Roja).
- Sub-módulo por "puntos" ambientales (`readings_readingambiental`): **sin
  datos desde dic-2024** (inactivo).
- Alertas: `alerts_limitalert`, `alerts_commentalert`, incidentes
  (`alerts_incidentalert` vacía).
- Sensible (NO exponer): `account_user`, `authtoken_token`, `django_session`,
  `historical_*history.data` (credenciales de dispositivos).

## 4. Configuración (`.env`)

Las credenciales de las dos bases están **separadas por prefijo** en `.env`
(modelo de referencia: `.env.example`). El usuario escribe los valores reales
en la máquina de trabajo:

```dotenv
# ---------- Base ENERGÍA ----------
ENERGIA_DB_HOST=127.0.0.1
ENERGIA_DB_PORT=5432
ENERGIA_DB_USER=postgres
ENERGIA_DB_PASSWORD=XXXXXXXX
ENERGIA_DB_NAME=energy
USE_SSH_TUNNEL_ENERGIA=false

# ---------- Base AMBIENTAL ----------
AMBIENTAL_DB_HOST=127.0.0.1
AMBIENTAL_DB_PORT=5433
AMBIENTAL_DB_USER=postgres
AMBIENTAL_DB_PASSWORD=XXXXXXXX
AMBIENTAL_DB_NAME=valhalladb
USE_SSH_TUNNEL_AMBIENTAL=false
```

Túneles SSH (solo en casa, `USE_SSH_TUNNEL_*=true`):

```dotenv
SSH_KEY_ENERGIA=energy.pem
SSH_USER_ENERGIA=ubuntu
SSH_HOST_ENERGIA=54.242.41.196
SSH_REMOTE_HOST_ENERGIA=172.31.29.136
SSH_REMOTE_PORT_ENERGIA=5432

SSH_KEY_AMBIENTAL=valhallaprod.pem
SSH_USER_AMBIENTAL=ubuntu
SSH_HOST_AMBIENTAL=ec2-44-206-41-101.compute-1.amazonaws.com
SSH_REMOTE_HOST_AMBIENTAL=172.31.29.136
SSH_REMOTE_PORT_AMBIENTAL=5432
```

Notas:
- Las claves `.pem` y el `.env` están en `.gitignore` (NUNCA commitear).
- Retrocompatibilidad: las variables genéricas `DB_*`, `SSH_*` siguen
  funcionando como alias de ENERGÍA (scripts antiguos no se rompen).
- En casa el túnel ambiental se expone en 5435 (configurado a mano); en el
  trabajo será 5433 local. Para probar en casa: `AMBIENTAL_DB_PORT=5435`.

## 5. Arranque en la computadora del trabajo (paso a paso)

1. **Instalar PostgreSQL 16** (Homebrew: `brew install postgresql@16`).
2. **Dos clusters/instancias locales**:
   - Energía en `127.0.0.1:5432` (cluster por defecto), base `energy`.
   - Ambiental en `127.0.0.1:5433` (segundo cluster o instancia), base
     `valhalladb`.
3. **Backup inicial**: en casa o en el trabajo, generar dumps de producción
   de ambas bases:
   ```bash
   scripts/sync_db.sh energia --dump-only
   scripts/sync_db.sh ambiental --dump-only
   ```
   (también sirve para traer datos recientes cuando se pida: `--dump-only`
   solo descarga; `full` descarga + restaura + verifica).
4. **Restaurar** en local:
   ```bash
   scripts/sync_db.sh energia --restore-only backups/energia/energy_prod_*.dump
   scripts/sync_db.sh ambiental --restore-only backups/ambiental/ambiental_prod_*.dump
   ```
   (en la máquina de trabajo los puertos locales son 5432/5433 según `.env`).
5. **Escribir credenciales** en `.env` (copiar desde `.env.example`).
6. **Verificar conectividad**:
   ```bash
   source venv/bin/activate
   python -c "from src import db; print(db.run_query('SELECT 1', base='energia')); print(db.run_query('SELECT 1', base='ambiental'))"
   ```
7. **Levantar la webapp**:
   ```bash
   python webapp.py        # → http://localhost:8000
   ```
   Usar el selector superior para alternar entre Energía y Ambiental.

## 6. Sincronización de datos (scripts)

`scripts/sync_db.sh <energia|ambiental> [--dump-only | --restore-only FILE]`

- `full` (default): túnel SSH → `pg_dump` completo → restaura local → verifica
  conteos de `readings_reading` y última lectura → limpia dumps viejos
  (conserva los últimos 3).
- `--dump-only`: solo descarga el dump (para llevar datos al trabajo).
- `--restore-only FILE`: solo restaura un dump existente (sin túnel).
- Cada base guarda sus dumps en `backups/<base>/`.

## 7. El agente

### 7.1 Comportamiento común (ambas bases)

- Loop OpenRouter con function calling (máx 15 iteraciones, temperature 0.1).
- Herramientas: `list_schemas`, `list_tables`, `describe_table`, `run_query`
  (SELECT-only con LIMIT automático y read-only) y `render_chart`
  (line/area/bar/pie; la web los dibuja con ECharts local).
- **Fecha inyectada** en el system prompt (hora Lima) para que "ayer"/"este
  mes" sean correctos.
- Regla de datos faltantes: si 1-2 consultas confirman que no hay datos,
  NO reintenta en loop: responde "sin datos", indica el rango disponible y
  ofrece alternativas.
- Presentación: español, cifras con unidad y periodo, tabla/lista primero,
  gráfico complementario, cierre "💡 Para tener en cuenta" con hallazgo
  respaldado por datos.
- Seguridad: solo lectura (sesión read-only + validación sintáctica), no
  exponer tablas sensibles.

### 7.2 Prompt de energía (`SYSTEM_PROMPT`)

- Mapa del esquema energy, trampas de timezone (Lima/UTC), contador
  `"EPpos_value"` = MAX−MIN, criterio Oechsle Salaverry (puntos 75+76),
  tarifas, facturas PDF 2025 (Kallpa), patrones de reporte (consumo base,
  P95, umbrales), chequeos de sanidad.

### 7.3 Prompt ambiental (`SYSTEM_PROMPT_AMBIENTAL`)

- Mapa de valhalladb (módulo ocupacional activo vs ambiental inactivo),
  nombres exactos (Sanna, San Borja, 13 salas), unidades y referencias
  (CO2 <800 ppm bueno, TEMP 20-27 °C, HUM 40-60%, PM2.5/PM10 OMS),
  cadencia 5 min y análisis de huecos (corte individual vs agrupado por
  sala, puntos perdidos), chequeos de sanidad, privacidad.

### 7.4 Modelo por defecto

`qwen/qwen3-coder` (mejor balance precisión/velocidad/costo según eval).
Selector web: DeepSeek V4 Flash, Gemini 2.5 Flash, GPT-4.1 mini,
Claude Sonnet 4.5 (premium).

## 8. Demo para la presentación

1. `source venv/bin/activate && python webapp.py` → http://localhost:8000
2. Módulo **Energía** → "¿Cuál fue el consumo de Sanna San Borja ayer?"
   (respuesta + 💡; alternar gráfico con pregunta horaria).
3. Módulo **Ambiental** → "¿Qué salas tuvieron CO2 por encima de 1000 ppm
   este mes?" y "¿Hubo cortes de monitoreo en agosto? Detállalos por sala"
   (usa el análisis de huecos del prompt).
4. Probar `/gaps` (energía, huecos de lecturas) si se quiere complementar.

## 9. Decisiones pendientes / próximos pasos

- [ ] Escribir credenciales reales en `.env` en la máquina de trabajo.
- [ ] Restaurar backups de ambas bases en 5432/5433 (o ejecutar sync full).
- [ ] Validar tarifas/criterios de energía con el equipo (Oechsle 39.15,
  Sanna sin tarifa registrada).
- [ ] Multi-tenant futuro: agente por cliente con su(s) base(s) y prompts
  propios (hoy: Sanna San Borja activa en ambas).
- [ ] Posibles módulos nuevos (control de dispositivos, reportes) en el
  prompt ambiental cuando tengan datos.
- [ ] Frontend por cliente (marca/logo) si la demo lo requiere.

## 10. Comandos útiles

```bash
source venv/bin/activate
python scripts/test_connection.py            # conectividad energía (túnel)
python cli.py --base ambiental               # chat ambiental en terminal
python cli.py --base energia                 # chat energía en terminal
python webapp.py                             # web multi-módulo → :8000
scripts/sync_db.sh ambiental --dump-only     # traer datos recientes ambientales
scripts/sync_db.sh energia full              # sync completa energía
```