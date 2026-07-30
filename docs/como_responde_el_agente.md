# Cómo responde el agente: proceso, columnas y datos relevantes

Este documento explica, paso a paso, qué hace el agente desde que el cliente
escribe una pregunta hasta que devuelve la respuesta, y **dónde vive cada dato**
que usa dentro de la base `energy`.

---

## 1. El proceso (loop del agente)

```
Cliente (español natural)
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. INTERPRETACIÓN (LLM vía OpenRouter)                       │
│    El modelo lee la pregunta junto con su system prompt,     │
│    que contiene: mapa del esquema, rutas de JOIN,            │
│    diccionario de indicadores, patrones SQL canónicos,       │
│    reglas de negocio y de presentación.                      │
│    Decide qué necesita consultar (no consulta "a ciegas").   │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. RESOLUCIÓN DE ENTIDADES (1–2 consultas pequeñas)          │
│    Traduce nombres a IDs: "Sanna San Borja" → eh.id = 1.     │
│    Tablas: enterprises_enterprise / energyheadquarter /      │
│    electricalpanel / devices_device / measurementpoint.      │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. CONSULTA DE DATOS (1–3 consultas analíticas)              │
│    run_query(sql) sobre readings_reading / alerts_alert /    │
│    tablas de facturación, con las columnas del indicador     │
│    pedido. Antes de ejecutar, la capa db.py:                 │
│      - valida que sea SELECT/WITH de una sola sentencia      │
│      - bloquea palabras de escritura (INSERT/DROP/…)         │
│      - inyecta LIMIT si falta y trunca resultados grandes    │
│      - ejecuta en sesión read_only con timeout de 120 s      │
│    Si el SQL falla, el error se devuelve al modelo y lo      │
│    corrige solo (autocorrección).                            │
└─────────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. CHEQUEO Y PRESENTACIÓN                                    │
│    El modelo aplica el "chequeo de sanidad" (rangos          │
│    razonables), genera render_chart con los valores reales   │
│    y redacta: análisis con cifras → gráficos →               │
│    "💡 Para tener en cuenta" (insight + recomendación).      │
└─────────────────────────────────────────────────────────────┘
```

Máximo 15 iteraciones por pregunta. Todo el SQL ejecutado queda visible en la
web ("Ver N consulta(s) SQL") y en los JSON de `eval/results/`.

## 2. Dónde está la data relevante

### A. Identidad y jerarquía (quién es quién) — tablas chicas

| Tabla | Columnas que usa el agente | Qué representa |
|---|---|---|
| `enterprises_enterprise` | `id`, `name` | Empresa (Sanna, Oechsle, KFC…) |
| `enterprises_energyheadquarter` | `id`, `name`, `is_active`, `energy_provider`, `billing_data_id`, `enterprise_id` | Sede (San Borja, Salaverry…) |
| `enterprises_electricalpanel` | `id`, `name`, `is_main`, `energy_headquarter_id` | Tablero (TGA (N), TG-RT…) |
| `devices_device` | `id`, `name`, `electrical_panel_id` | Analizador de red físico |
| `enterprises_measurementpoint` | `id`, `name`, `is_main`, `type`, `channel`, `device_id` | Circuito medido (llave general, Tomógrafo…) |

**La cadena que une todo** (la usa en casi cada consulta):
`readings_reading → measurementpoint → device → electricalpanel →
energyheadquarter → enterprise`.

### B. Series temporales (el corazón) — `readings_reading` (~8.3M filas)

1 lectura por minuto por circuito. Claves: `measurement_point_id`,
`device_id`, `created_at` (timestamptz **UTC** → se convierte con
`AT TIME ZONE 'America/Lima'`).

Columnas por indicador (case-sensitive, siempre con comillas dobles):

| Pregunta del cliente | Columna(s) | Cálculo típico | Unidad |
|---|---|---|---|
| consumo / energía | `"EPpos_value"` (y `"EPneg_value"` exportada) | **MAX − MIN en el periodo** (es contador) | kWh |
| demanda / potencia | `"P_value"` | AVG, MAX; ÷1000 | kW |
| corriente | `"Ia_value"`, `"Ib_value"`, `"Ic_value"`, `"In_value"` | AVG/MAX por fase | A |
| voltaje | `"Ua/Ub/Uc_value"` (fase-neutro), `"Uab/Ubc/Uac_value"` (fase-fase) | AVG/MIN/MAX | V |
| factor de potencia | `"PF_value"` | AVG, % tiempo < 0.9 | 0–1 |
| reactiva | `"Q_value"` (instantánea), `"EQpos_value"` (contador) | AVG / MAX−MIN | var / kvarh |
| aparente | `"S_value"` | AVG | VA |
| frecuencia | `"F_value"` | AVG/MIN/MAX | Hz |
| armónicos / THD | `"THDUa/b/c_value"`, `"THDIa/b/c_value"` | AVG, P95 | % |
| desbalance | calculado de Ia/Ib/Ic | (max−min)/promedio×100 | % |

Notas que el agente ya conoce: lecturas en 0/NULL cuando el equipo no reporta
(se filtran); `"P_value"` viene en watts; EPpos solo crece (contador).

### C. Alertas — `alerts_alert` (317K) + configuración

| Tabla | Columnas clave |
|---|---|
| `alerts_alert` | `timestamp`, `alert_status` (moderate/critical), `status`, `value`, `notes` (descripción legible), `fluctuation_subtype` (overvoltage/undervoltage), `power_subtype`, `current_subtype` (zero_current, current_anomaly, max_current_exceeded), `energy_subtype`, `unbalanced_subtype`, `alert_threshold_id`, `reading_id` |
| `alerts_alertthreshold` | `alert_type` (voltage_fluctuation, current_monitoring, power_demand, harmonic_distortion, energy_monitoring), `threshold_value`, y los enlaces directos `enterprise_id` / `energy_headquarter_id` / `electrical_panel_id` / `measurement_point_id` |
| `alerts_energythresholdprofile` | límites calculados por punto/sede: `max_demand_kw`, `contracted_power_kw`, límites de THD, CUF/VUF, bandas de energía por tipo de día |

### D. Facturación y contrato

| Tabla | Columnas clave |
|---|---|
| `enterprises_billingdata` | `charge_for_active_energy_peak` / `_off_peak` (S//kWh), `monthly_fixed_charge`, cargos por potencia, `charge_for_reactive_energy_exceeding_30_percent`, `currency` (PEN) |
| `enterprises_billingcycle` | `start_date`, `end_date`, `is_current`, `energy_headquarter_id` |
| `enterprises_power` | `power_contracted` (kW contratados), `power_installed`, `power_max` |

### E. Prohibidas (nunca se exponen)

`historical_readinghistory.data` (credenciales de dispositivos en crudo),
`accounts_user`, `authtoken_token`, `django_session`.

## 3. Traza real de ejemplo

Pregunta: *"¿Cuál fue el consumo de Sanna San Borja el 20 de julio de 2026?
Detalla por punto principal."* (traza real del 28-jul, modelo qwen3-coder)

**Paso 2 — resolver entidades** (tablas chicas, instantáneo):
```sql
SELECT e.id, e.name, eh.id, eh.name
FROM enterprises_enterprise e
JOIN enterprises_energyheadquarter eh ON eh.enterprise_id = e.id
WHERE e.name = 'Sanna' AND eh.name = 'San Borja'
-- → eh.id = 1
```

**Paso 3 — consulta de datos** (la pesada, ~5–10 s):
```sql
SELECT mp.id, mp.name,
       MAX(r."EPpos_value") - MIN(r."EPpos_value") AS kwh
FROM readings_reading r
JOIN enterprises_measurementpoint mp ON r.measurement_point_id = mp.id
JOIN devices_device d             ON mp.device_id = d.id
JOIN enterprises_electricalpanel ep ON d.electrical_panel_id = ep.id
WHERE ep.energy_headquarter_id = 1                -- sede resuelta
  AND r.created_at >= '2026-07-20 00:00:00-05'    -- día local Perú (UTC-5)
  AND r.created_at <  '2026-07-21 00:00:00-05'
  AND mp.is_main = true                            -- solo llaves generales
  AND r."EPpos_value" IS NOT NULL
GROUP BY mp.id, mp.name
```

**Paso 4 — salida**: tabla con los kWh por punto → `render_chart` (barras)
→ sección 💡 ("Llave general TGA concentra 97.9%…"). Total: 2 consultas SQL,
~15 s, ~$0.002.

## 4. Cuánto tarda según lo pedido (medido)

| Alcance de la consulta | Tiempo DB | Tiempo total al cliente |
|---|---|---|
| Entidades/rankings simples | < 1 s | 5–15 s |
| Un día/semana, 1 sede | 5–15 s | 10–30 s |
| 1 mes × 1 punto | ~10 s | 15–40 s |
| 3 meses × 2 puntos | 60–100 s | 80–240 s |
| 3 meses × 2 tableros | 60–100 s | 80–240 s |

El cuello de botella es la DB (sin índice covering sobre las columnas de
medición), no el modelo. Costo típico: $0.002–0.04 por pregunta.
