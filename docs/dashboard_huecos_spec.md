# Dashboards de huecos de lectura — especificaciones

Especificación del trabajo realizado (sesiones 14–16, bitácora): dashboards web
para visualizar la **cobertura** y las **pérdidas de datos** de las dos bases del
proyecto, más los pipelines que generan sus datos.

| Base | Servidor | Dashboard | Fuente principal |
|---|---|---|---|
| **energia** (`energy`) | `python webapp.py` → :8000 | `/gaps` | `readings_reading` (~9.2M filas, 2025-12→2026-08-17 en copia local) |
| **ambiental** (`valhalladb`) | `PORT=8002 python webapp_ambiental.py` → :8002 | `/gaps` | `readings_reading` (15.7M filas, **viva**) + `readings_readingambiental` (885 filas, pruebas 2024) |

Ambos usan ECharts local (`web/static/vendor/echarts.min.js`), tema oscuro,
filtros, auto-carga con feedback "Actualizando…" y verificación end-to-end con
Playwright (Chromium).

---

## 1. Conceptos comunes

### 1.1 Cadencia medida = presencia de lectura, no valor
Los heatmaps NO grafican valores energéticos (`P_value`, `EPpos_value`): cuentan
**llegada de lecturas**. Verificado en DB: cada fila trae `EPpos`, `P` e `Ia`
siempre juntos (mismos conteos exactos), mientras `PF`/`Ua`/`F` vienen NULL en
estos equipos. La cadencia se cuenta sobre `count("EPpos_value" IS NOT NULL)`
para blindar el análisis si algún dispositivo reportara filas sin contador.
Una falta = se perdió el paquete completo de indicadores de ese punto/combo.

### 1.2 Hora Lima
Todo tiempo se calcula en `America/Lima` (`AT TIME ZONE 'America/Lima'`),
incluyendo los bordes de rango: `(CAST(fecha AS date))::timestamp AT TIME ZONE
'America/Lima'`. Nunca comparar `created_at` contra un `::date` pelado.

### 1.3 Horizonte de datos (anti-falso-hueco)
La última lectura de cada punto/combo delimita lo **evaluable**: los minutos/días
posteriores se pintan **gris claro ("fin de datos")**, no rojo, y no cuentan como
hueco. Motivación real: la copia local de energía termina el 17-ago-2026 14:33
Lima (los 7 puntos con datos cortan entre 14:32:57 y 14:33:50 → corte del dump,
no caída de medidores). El backend entrega ese horizonte en `meta`.

### 1.4 Umbrales de estado diario
- **Energía**: fijo — 1 lectura/min ⇒ día completo ≥1300 lecturas (≥90%),
  parcial 10–1299, hueco <10. Mediana real: 1,393/día/punto (97%).
- **Ambiental**: relativo al ritmo propio de cada sensor (mediana histórica;
  ~288/día = 1 cada 5 min) — completo ≥90% del ritmo, parcial ≥10%, hueco <10%
  (mínimo 5 lecturas).

### 1.5 Seguridad
Toda consulta es SELECT-only contra sesiones read-only (`statement_timeout`
120s energía / 300s ambiental). Límites por request. `.env` y `*.pem`
gitignored; sin secretos en el repo.

---

## 2. Dashboard energía — `http://localhost:8000/gaps`

Pestañas:

1. **Cobertura diaria** — KPIs + heatmap punto×día (verde/ámbar/rojo) + tabla
   por punto (rango, días completos/parciales/hueco, episodios, staleness con
   badge "sin datos N+ días"). Datos de archivos exportados.
2. **Minuto a minuto** — heatmap x=minuto del día (00:00–23:59), fila=punto·fecha.
   Verde=llegó, rojo=no llegó, gris=sin datos, gris claro=fin de la copia.
   Zoom por horas (dataZoom), tooltip con HH:MM exacto, tabla-resumen por punto:
   buckets con/faltantes, % cobertura, **último dato**, racha más larga
   (HH:MM–HH:MM) y ventanas >5 min. Selección 1–3 puntos o checkbox
   **"Comparar todos los puntos"** (auto-salta al último día completo).
3. **Eventos agrupados / Huecos individuales** — pipeline fino de minutos
   (tablas `analisis_eventos` / `analisis_huecos`, sesión 14).

Endpoints añadidos a `webapp.py`:

| Endpoint | Descripción |
|---|---|
| `GET /api/cobertura` | estado día×punto desde `analisis/cobertura_diaria.csv` (filtros empresa/punto/desde/hasta) |
| `GET /api/cobertura/resumen` | resumen por punto + episodios + métricas desde `analisis/cobertura_resumen.json` |
| `GET /api/lecturas?puntos=76,75&desde=&hasta=` | **en vivo**: minutos Lima con lecturas (≤3 puntos, ≤10 días). SQL agrupa por punto/día/hora/minuto sobre índice `(measurement_point_id, created_at)` filtrando `EPpos_value IS NOT NULL`; devuelve `meta` con horizonte por punto (`ultimo_dia`, `ultimo_min_idx`, `ultima_hhmm`). ~0.2 s por punto-día |

Pipeline: `venv/Scripts/python.exe scripts/analisis_huecos.py [--intraday] [--export]`
→ `analisis/cobertura_diaria.csv` (6,737 filas punto×día) +
`analisis/cobertura_resumen.json` (85 puntos).

## 3. Dashboard ambiental — `http://localhost:8002/gaps`

Replique del mismo diseño, adaptado a sensores ambientales. Selector de fuente
arriba: **Salas** (`readings_reading`, track real) o **Puntos ambientales**
(`readings_readingambiental`, solo 885 lecturas nov–dic 2024).

Pestañas:

1. **Cobertura diaria** — KPIs (269 combos, 254 con datos, 19,142 días-hueco,
   mayor hueco 377 días) + heatmap combo×día (estado **relativo al ritmo del
   sensor**) + tabla (ritmo/día, episodios, staleness).
2. **Lectura a lectura** — igual que energía pero con **bucket adaptativo**:
   columna = ventana de captura `round(1440/ritmo)` min (≈5 min aquí; no minuto
   suelto porque la cadencia no es 1/min). Umbrales y ventanas (>15 min)
   escalan con el bucket. Selección 1–3 combos o comparar-todos (≤40),
   auto-carga, feedback y duración visibles.
3. **Episodios** — todos los episodios de días-hueco ordenados por duración.

Endpoints de `webapp_ambiental.py` (puerto `PORT`, actual 8002):

| Endpoint | Descripción |
|---|---|
| `GET /gaps` · `/reporte` | dashboard nuevo · reporte simple clásico |
| `GET /api/ambiental/cobertura/resumen?track=salas\|puntos` | resumen por combo |
| `GET /api/ambiental/cobertura/diaria?track=&empresa=&lugar=&desde=&hasta=` | estado día×combo |
| `GET /api/ambiental/lecturas?track=&combos=&desde=&hasta=` | **en vivo** contra valhalladb (índice `idx_reading_indicator_device_room_created_at`), meta horizonte por combo, ≤40 combos / ≤10 días |
| `GET /api/ambiental/huecos/salas` · `/puntos` | compatibilidad con clientes existentes |

Pipeline: `venv/Scripts/python.exe scripts/analisis_huecos_ambiental.py --export`
→ `analisis/huekos…` (resumen salas/puntos) +
`analisis/cobertura_diaria_ambiental_{salas,puntos}.json` (70,877 días-combo en
salas; incluye días 0-lectura dentro de cada ventana).

---

## 4. Hallazgos principales (agosto 2026)

**Energía** (datos hasta 17-ago 14:33 Lima):
- 36 de 85 puntos con lecturas (31 activos: 18 Oechsle + 13 Sanna). Los 49
  restantes pertenecen a dispositivos de prueba ("Device falso"), no son huecos.
- Burger King: hueco de **110 días** (12-abr→30-jul) y caído desde el 31-jul.
- Pizza Hut: hueco de 8 días (11→18-abr) antes de morir el 20-abr.
- Madam Tusan: vida corta (20-abr→14-may) con 4 episodios de 1–2 días.
- Sanna: caída global de ~3.5 días (**3-may 04:40 → 6-may 16:08 Lima**) en TODOS
  sus puntos (evento externo); hueco de 5 días en cuarto de bombas (26-feb→2-mar);
  4 días al arranque de las pinzas TGA (dic-2025).
- Oechsle: **0 días-hueco** (la más limpia); cortes intra-día puntuales que
  afectan a los 4 medidores a la vez (24-jun 2.4 h, 15-jul 4.3 h, 16-jul 2.1 h)
  → cortes de planta, no de dispositivos.
- El bloque "rojo desde las 14:33" del 17-ago es **fin de la copia local**
  (sync pendiente), no apagón.

**Ambiental** (base viva hasta HOY):
- Track salas: 269 combos sensor×sala (CO2/TEMPERATURE/HUMIDITY/luz/PM…);
  19,142 días-hueco acumulados; mayor hueco 377 días; despliegues intermitentes
  (cada combo tiene su propia ventana temporal).
- Sensores activos San Borja reportan cada ~5 min (12 lecturas/hora, 288/día).
- Track puntos: prácticamente sin uso (885 lecturas en una semana de 2024).
- Datos de prueba con sedes nombradas literalmente "true"/"false" y combos
  inactivos con histórico (Pisco-ICA/Aceros Arequipa).

---

## 5. Lecciones técnicas registradas

1. `GROUP BY` no devuelve días/minutos SIN filas → hay que calendarizar el
   rango completo y usar `get(dia, 0)` o el complemento, o los huecos reales
   son invisibles.
2. Mostrar secciones con `style.display = ""` NO anula un `display:none` de la
   hoja de estilos → usar `"block"/"none"` explícitos (causó el dashboard "en
   blanco" con todo renderizado en DOM).
3. En ECharts, `axisLabel.interval` es por ÍNDICE de categoría;
   `Number("00:00")` es NaN → formatter por índice, no por valor.
4. Chrome headless colgado ≠ página rota: verificar con Playwright
   (`venv/.../pip install playwright && python -m playwright install chromium`)
   antes de tocar código por intuición.
5. Al pushear a ramas compartidas, revisar primero qué trae el remoto: el
   cherry-pick sobre `origin/main` evitó perder el trabajo multi-base ajeno.

---

## 6. Cómo reproducir

```bash
# Energía
venv/Scripts/python.exe scripts/analisis_huecos.py --export
venv/Scripts/python.exe webapp.py                # http://localhost:8000/gaps

# Ambiental
venv/Scripts/python.exe scripts/analisis_huecos_ambiental.py --export
PORT=8002 venv/Scripts/python.exe webapp_ambiental.py                   # http://localhost:8002/gaps
```

QA: scripts de Playwright bajo `/tmp` (`pw_min.py`, `pw_horizon.py`,
`pw_todos*.py`, `pw_amb*.py`) — cargar cada tab, accionar filtros y capturar
consola/errores.
