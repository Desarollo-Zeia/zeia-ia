# Bitácora del proyecto ZEIA-IA

Registro cronológico de sesiones de trabajo: qué se hizo, qué se descubrió y
qué decisiones se tomaron. **Actualizar al final de cada sesión.**

---

## Sesión 1 — 2026-07-28: Conectividad + agente base + comparativa de modelos

### Objetivo
Agente IA que navega la DB `energy` (PostgreSQL 16, producción) vía túnel SSH
y responde preguntas de negocio. Stack elegido: Python + CLI + agente con
herramientas SQL + OpenRouter.

### Conectividad (manual de empresa tenía 3 errores)
- Túnel: `ssh -i energy.pem -N -L 55432:172.31.29.136:5432 ubuntu@54.242.41.196` ✔ funciona.
- Credenciales correctas: usuario `postgres` (NO `Postgres`), base `energy`
  (NO `Energy`), password `Zeia@2026.` (la del manual, `Valhalla*2020DB`,
  ya estaba rotada). El `@` del password obligó a usar `URL.create` en SQLAlchemy.
- Puerto pgAdmin del manual (5435) era typo; el túnel expone 55432.

### Conocimiento del dominio descubierto
- 63 tablas, esquema `public`. App Django.
- Jerarquía: `enterprises_enterprise` → `enterprises_energyheadquarter` (sedes)
  → `enterprises_electricalpanel` (tableros) → `devices_device`
  → `enterprises_measurementpoint` (circuitos, 85).
- `readings_reading`: ~8.3M filas, 1 lectura/min/punto desde dic-2025.
  Columnas de medición **case-sensitive con comillas**: `"P_value"` (W),
  `"EPpos_value"` (contador kWh acumulado), `"PF_value"`, `"Ua_value"`, etc.
- **EPpos es contador acumulado → JAMÁS SUM(); consumo = MAX−MIN** por punto
  y periodo (para punta/fuera de punta, agrupar por punto Y DÍA primero).
- Empresas: Sanna (San Borja/San Isidro), Oechsle/Pizza Hut/Burger King/KFC
  (3 sedes distintas llamadas 'Salaverry' — filtrar por eh.id, el texto
  "Oechsle Salaverry" NO existe), Madam Tusan (Óvalo Gutierrez),
  TestCorp_Email (ignorar), BanBif (sin infraestructura).
- **Datos vivos solo en Sanna y Oechsle** (al 28-jul-2026). Madam Tusan hasta
  14-may-2026; Pizza Hut 20-abr; KFC/BK 11-abr. → regla anti-alucinación:
  "sin datos" + ofrecer rango disponible.
- Alertas desde may-2026 (317K): tipos voltage_fluctuation, current_monitoring,
  power_demand, harmonic_distortion, energy_monitoring.
- Sensible: `historical_readinghistory.data` tiene credenciales de dispositivos
  en texto plano → prohibido exponer (regla en prompt).
- **Tarifa Oechsle = 39.15 S//kWh** en `enterprises_billingdata`: casi seguro
  error de digitación (las demás 0.65). El agente lo reporta con transparencia.
- Potencia contratada: San Borja 1200 kW, Oechsle Salaverry 686 kW.

### Comparativa de modelos (eval/results/run_20260728_170746.json)
60 corridas (6 modelos × 10 preguntas). Veredicto:
- **qwen/qwen3-coder → DEFAULT** (alta calidad, honesto, 18s/preg, $0.05/10)
- gemini-2.5-flash: rápido/barato (9s) pero errores numéricos puntuales
- gpt-4.1-mini: buena alternativa
- claude-sonnet-4.5: la mejor calidad, 35× más caro → tier premium
- deepseek-v3.2: buena calidad pero 182s/preg (hasta 8 min) → inusable en chat
- llama-3.3-70b: **descartado** (alucina: "NaN kW", rompe formato de tools)

### Lecciones de prompt engineering (críticas)
1. Inyectar mapa del esquema + rutas de JOIN en el prompt: deepseek pasó de
   13→4 iteraciones y de $0.018→$0.004 por pregunta.
2. Regla "0 filas ≠ sin datos": primero verificar filtros (nombres exactos).
3. Chequeo de sanidad: rangos razonables (sede 1k–50k kWh/mes, retail grande
   ~400k; tarifas 0.4–0.7 S//kWh; demanda 1–200 kW).
4. Ejemplos de patrones SQL canónicos en el prompt (contador MAX−MIN, punta/
   fuera de punta por día) evitan errores de 3 órdenes de magnitud.

---

## Sesión 2 — 2026-07-28 (tarde): Gráficos + app web + catálogo de negocio

### Qué se construyó
- `docs/question_catalog.md`: 6 categorías de valor (facturación, anomalías,
  patrones, ahorro, comparativas, salud del monitoreo) con insights proactivos.
- Herramienta `render_chart` (line/bar/area/pie) capturada en `agent.py`;
  respuesta con sección proactiva "💡 Para tener en cuenta".
- `webapp.py` (FastAPI) + `web/static/index.html`: chat web con ECharts,
  selector de modelo, chips de preguntas, SQL desplegable. → :8000

### Bugs encontrados y corregidos
- **Gráfico vacío en el navegador**: librerías por CDN → ahora locales en
  `web/static/vendor/`; sanitización de specs en backend (números con comas,
  campos faltantes); error visible dentro de la caja del gráfico.
- **Respuesta solo-insights**: tras llamar render_chart los modelos "se ponían
  flojos" y solo escribían el 💡. Fix doble: (1) el tool responde "el gráfico
  COMPLEMENTA, redacta tu respuesta completa"; (2) "RECORDATORIO FINAL" al
  final del prompt (efecto recencia). Verificado 2/2 en qwen y gemini.
- Python 3.9: pydantic no acepta `str | None` → usar `Optional[str]`.
- Charts por respuesta se cortaban con los de la sesión → snapshot por `ask()`.

---

## Sesión 3 — 2026-07-28 (noche): Capacidades, carga y reportes

### Preguntas del usuario
1. ¿Qué tipos de indicadores puede consultar el cliente? → diccionario en
   `docs/question_catalog.md` y en el prompt (aliases: "corriente" → Ia/Ib/Ic…).
2. ¿Cuánta data puede pedir (p. ej. 3 meses × 2 puntos/tableros), cuánto tarda
   y cuánto cuesta? → prueba medida abajo.
3. Análisis de 12 láminas de reporte mensual real (Sanna San Borja, junio)
   → `docs/reportes_analisis.md`.

### Rendimiento de la DB en analíticas pesadas (medido directo)
- Índices existentes sobre `readings_reading`: varios btree por
  (measurement_point_id, created_at), BRIN por created_at, parciales por
  P/EPpos no nulos. Ninguno COVERING con EPpos/P → las agregaciones tocan heap.
- 3 meses × 1 punto (~123k lecturas): **~25s**.
- 3 meses × 2 puntos con `IN`: **69–101s** (errático: el planner cambia de
  estrategia; una consulta de panel completo con 14 puntos tardó 17s).
- `statement_timeout` subido de 30s → **120s** en `db.py` por este motivo.
- **Recomendación al equipo (DBA)**: índice covering
  `(measurement_point_id, created_at) INCLUDE ("EPpos_value","P_value")`
  o tabla pre-agregada horaria/diaria. Bajaría estas consultas a segundos.

### Prueba de carga con modelos (3 meses × 2 puntos/tableros)
Archivo: `eval/questions_carga.yaml`, resultados en `eval/results/run_20260728_202007.json`.
Ground truth (computado directo):
- mp40 Data center UPS: may 6,677 / jun 6,509 / jul 6,012 kWh
- mp34 TN-P-12: may 4,289 / jun 4,240 / jul 3,752 kWh
- TGA llave (mp4): may 72,277 / jun 71,373 / jul 64,629 kWh
- TG-RT Tomógrafo (mp2): may 20,589 / jun 20,313 / jul 18,745 kWh (TGA ≈ 77.8%)

Resultados (3 modelos económicos × 2 preguntas de carga):

| Modelo | c1 (2 puntos) | c2 (2 tableros) | t_total | costo |
|---|---|---|---|---|
| gpt-4.1-mini | ✅ exacto | ✅ exacto (77.5–77.9%) | 236s | $0.046 |
| qwen3-coder | ✅ exacto | ❌ 1.74M kWh (sumó subcircuitos → 24× inflado) | 163s | $0.031 |
| gemini-2.5-flash | ❌ solo insights, sin cifras | ✅ correcto | 167s | $0.025 |

Conclusiones:
- **gpt-4.1-mini fue el más confiable en analítica pesada** (2/2 correctas
  a la primera); gemini 1/2; qwen falló c2 de 3 formas distintas incluso
  tras afinar el prompt (sumar subcircuitos → duplicar valores → loop).
  → Recomendación de routing: chat general → qwen3-coder (rápido, bueno);
  analítica pesada multi-entidad (tableros, varios meses) → **gpt-4.1-mini**;
  reportes premium → claude-sonnet-4.5.
- Regla de prompt que SÍ quedó (correcta y necesaria): consumo de tablero =
  punto is_main si existe; si no, suma de puntos; nunca llave + subcircuitos;
  total sede = Σ llaves de sus tableros. Ojo: TG-RT no tiene punto is_main
  (su carga principal es el punto "Tomógrafo").
- **La DB es el cuello de botella, no el LLM**: el GT de c1 tomó 68.5s de
  puro SQL. El agente tarda ~80–240s en total (LLM añade 20–60s).
- **Costo por pregunta pesada: $0.006–0.04** (1–4 centavos de dólar).
  Una conversación típica de 5 preguntas ≈ $0.03–0.15. Toda esta sesión de
  pruebas de carga costó < $0.30.
- `statement_timeout` a 120s (en db.py) es lo que permite estas consultas.
- Gemini recayó una vez en el modo "solo insights" → quirk documentado.

### Análisis de reportes (12 láminas)
Ver `docs/reportes_analisis.md` — estructura, patrones analíticos adoptados
en el prompt, observaciones (tarifa implícita ~0.155 S//kWh ≠ billingdata) y
oportunidades de producto.

### Doc de funcionamiento interno
`docs/como_responde_el_agente.md` — proceso del agente paso a paso, mapa de
tablas/columnas relevantes por indicador, traza real de ejemplo y tiempos
medidos por alcance de consulta.

### BUG REPORTADO POR EL USUARIO: mismo día, distinto valor según la ventana
Síntoma: 21-jul daba 155.41 kvarh al pedir "7 días" pero 811 al pedir "14".
Investigación reveló TRES bugs de ventanas de tiempo (todos corregidos en
prompts.py, sección "Ventanas de tiempo"):

1. **Ventana rodante**: el modelo usaba `now() - interval '7 days'` → el
   primer día de la ventana quedaba cortado (112 lecturas vs ~1,380 de un día
   completo) y se presentaba como "día anómalo bajo". Fix: siempre días
   calendario locales completos.
2. **TRAMPA DE CASTEO UTC (la más sutil y peligrosa)**: comparar
   `created_at` (timestamptz) contra `(now() AT TIME ZONE 'America/Lima')::date`
   pelado → Postgres castea el date usando la TZ de sesión (UTC), así que
   `< '2026-07-28'` significa `< 2026-07-28 00:00 UTC` = el último día local
   queda cortado a las **19:00 Lima** (1,098 lecturas → 639 kvarh en vez de
   816). Fix: AMBOS bounds deben ser `::timestamp AT TIME ZONE 'America/Lima'`
   (o literales con offset '-05'). Verificado en DB.
3. **Hoy siempre es parcial**: excluir por defecto ("últimos N días" =
   N días completos terminando AYER: `- N` y `< hoy`); si se incluye,
   etiquetar "(hoy, parcial)" y nunca compararlo ni llamarlo anomalía.

Regla de oro añadida: antes de reportar un día como "anómalo", verificar que
esté completo (~1,380 lecturas/punto/día); día incompleto = "datos
incompletos", JAMÁS "consumo anormal bajo".

Prueba de regresión (flujo exacto del usuario, 2 turnos): 7 días → 07-21..27
con 07-21 = 811.00 ✓ consistente con la ventana de 14 días.

## Sesión 4 — 2026-07-30: Bug de reconexión del túnel SSH

Síntoma: la webapp respondía "problemas de conexión con la base de datos"
tras llevar un tiempo corriendo.

Causa raíz: `tunnel.ensure_tunnel()` solo se llamaba al crear cada
`EnergyAgent` (nueva sesión de chat). Si el proceso ssh moría después
(latencia/hibernación de la laptop, o keepalive de ssh que aborta a los
~90s sin respuesta), nada lo relanzaba y todas las consultas fallaban.
El docstring de tunnel.py decía "con reconexión" pero no existía tal lógica.

Fix (mínimo): `db.get_engine()` ahora llama `tunnel.ensure_tunnel()` en
cada acceso (antes de cualquier consulta). Si el puerto 55432 ya está
abierto el chequeo cuesta <1ms; si el túnel murió, se relanza solo.
Docstring de tunnel.py corregido.

Verificado: túnel caído → `run_query('SELECT 1')` lo relanza y responde OK;
webapp reiniciada (PID viejo matado), puertos 8000 + 55432 escuchando.
