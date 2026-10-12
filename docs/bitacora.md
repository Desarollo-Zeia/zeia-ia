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

## Sesión 5 — 2026-08-05: Análisis de documentos PDF (facturas Kallpa 2025)

### Objetivo
Que el agente pueda leer y comparar documentos PDF del proyecto (facturas
mensuales de energía) para extraer patrones, concordancias y anomalías,
complementando el análisis de la DB.

### Lo que se hizo
- **Módulo `src/pdf_tools.py`**: lista los PDFs de la raíz del proyecto y
  extrae su texto como Markdown. Motor principal: **pdf-inspector** de
  Firecrawl (`pip install pdf-inspector`, wheels Windows x64) — clasifica el
  PDF (text_based/scanned), respeta orden de lectura multi-columna y detecta
  tablas (41 ms por factura); fallback: pypdf. Validación de path-traversal
  (solo basename en la raíz).
- **Dos herramientas nuevas** en `src/tools.py`:
  `list_documents` (inventario de PDFs) y `extract_pdf_text` (Markdown con
  tablas, rango de páginas opcional, tope 18k chars).
- **System prompt** (`src/prompts.py`): sección nueva que documenta las
  facturas Kallpa (cliente "TIENDAS PERUANAS S.A." — Salaverry = Oechsle),
  su contenido (energía HP/HFP, potencia, demanda máx, factor de carga,
  peajes, FISE/LER/FOSE, totales US$/S/) y el patrón de comparación mensual
  (mismos campos por mes → serie → patrones/concordancias/anomalías), con
  contraste contra la DB y render_chart.
- **UI**: chip nuevo en el selector de preguntas ("Compara las facturas
  mensuales 2025…").
- `requirements.txt`: + pypdf, + pdf-inspector.

### Hallazgos
- Los "reportes mensuales" de Descargas resultaron ser las **facturas de
  Kallpa Generación** (1 página c/u), NO los reportes de 12 láminas de
  docs/reportes_analisis.md.
- La extracción con pdf-inspector es claramente superior a pypdf para estos
  PDFs: el texto sale en orden de lectura correcto y las tablas en Markdown;
  pypdf las sacaba en orden invertido.
- Prueba de punta a punta (enero vs marzo vs junio 2025): el agente extrajo
  los datos, armó tabla comparativa (MWh, costo S/, demanda máx, factor de
  carga), detectó tendencias y generó gráfico. 58k tokens de prompt para 3
  facturas (~58k/12 ≈ 4.8k tokens por factura + contexto de tools/prompt).

### Pendiente
- Contrastar lo facturado por Kallpa vs lo medido en DB (Oechsle Salaverry):
  el prompt ya invita a hacerlo; verificar que el dato de la factura
  (221.28 MWh en enero-2025) sea consistente con las lecturas (dic-2025 en
  adelante).

## Sesión 6 — 2026-08-05: Comparación facturas Kallpa 2025 vs monitoreo (Oechsle)

Análisis de concordancia en 3 ejes (pedido del cliente, ejecutado en el chat
del agente, 3 turnos encadenados):

**1. Consumo**: facturas 2025 → 183.03 (set) a 248.20 (mar) MWh/mes, prom
~212; monitoreo 2026 (mar-jul, meses completos) → 218–247 MWh/mes, prom
~228. Marzo 2026 (246.98) vs marzo 2025 (248.20) casi idénticos →
CONCORDANTE en magnitud y estacionalidad. Sin solape mes a mes (facturas
2025, lecturas desde 07-feb-2026); feb-2026 monitoreado = 100.3 MWh (parcial).

**2. Tarifa**: billingdata Oechsle = **39.15 USD/MWh** (currency USD, unit
MWh) → la "anomalía 39.15" de la bitácora se REINTERPRETA: no es error de
digitación S//kWh, es tarifa de mercado libre (CLIENTES LIBRES). Aun así
queda **11.98% por debajo** de lo real facturado en enero-2025 (43.84
US$/MWh blended; 44.04 HP / 43.78 HFP). S//kWh implícito TOTAL 2025 (energía
+ potencia + peajes + ley + IGV): **0.215–0.284, prom ~0.25** — 1.7× el 0.155
que asumen los reportes (Sanna, no Oechsle).

**3. Potencia**: contratada 686 kW (DB) vs demanda máx HFP enero-2025
688.16 kW → el tope se tocó (potencial recargo, la factura no muestra
exceso); peaje principal facturado sobre 540 kW (demanda coincidente) → se
paga capacidad de más en el contrato. Máx HP del año: 672.82 (dic).

**Aprendizaje para el prompt (FALLO DE EXTRACCIÓN)**: el agente tomó como
"Total S/" el subtotal de peajes (49,088.67 en enero) en vez del "Total a
pagar S/" general (57,718.28) — las facturas tienen varios "Total S/."
(peajes, FISE, LER, FOSE). El total general es el MAYOR número del bloque
"Resumen". → Añadir nota al prompt: usar siempre el mayor valor del bloque
Resumen como total general de la factura.

## Sesión 7 — 2026-08-05: Evaluación facturas Kallpa 2026 (ene-jun) vs monitoreo

Cliente copió 6 facturas 2026 (enero-2026.pdf … junio-2026.pdf) desde
Descargas. AHORA hay meses idénticos para comparación exacta.

### Hallazgos de concordancia (verificados con SQL directo)
- **Consumo**: monitoreado vs facturado (mes calendario, ciclos confirmados en
  DB): feb −53% (monitoreo arrancó 07-feb, parcial), mar +9.3% (246.96 vs
  225.96), abr −3.7%, may −5.0%, jun −6.5%. Diferencia residual ~4-9% →
  probable desfase de periodo de lectura del BRG o EPpos; pendiente validar
  contra el medidor de Kallpa.
- **Sin doble conteo**: las 4 llaves is_main (75,67,76,69) son alimentadores
  paralelos independientes (serie diaria verificada día a día) → su suma SÍ es
  el total de la sede.
- **Tarifa 2026**: 38.18→39.20 US$/MWh (HP=HFP), bajó ~12% vs 2025 (43.84).
  En MAYO-2026 el precio facturado (39.15) = EXACTAMENTE el billingdata
  registrado (39.15 USD/MWh) → la tarifa registrada quedó VALIDADA para 2026
  (en 2025 quedaba 12% abajo porque el precio real era otro).
- **Potencia**: DM HFP enero (692.54) y marzo (696.65) > 686 contratados →
  tope superado, pero Kallpa NO cobró exceso (Exceso HP/HFP = 0.000 en las
  facturas). Demanda coincidente facturada 536-566 kW << 686 → sobra
  capacidad contratada (~120-150 kW).
- S//kWh implícito total 2026: 0.225–0.248 (S/ 52-58 mil/mes).

### ⚠️ DATO IMPORTANTE DE CALIDAD (NUEVO)
**P_value tiene unidades INCONSISTENTES entre dispositivos**: Sanna "Llave
general TGA" está en kW (avg 91.8 → 2,203 kWh/día ✓ vs EPpos 2,197), Sanna
"cuarto de bombas" en W (avg 1,907 → 45.8 kWh/día ✓ vs EPpos 45.32), y
Oechsle en unidades raras (avg ~1.8, máx ~400; nada cuadra con EPpos) →
**NO usar P_value de Oechsle para demanda/potencia** hasta calibrar
unidades. La demanda máxima NO es validable contra facturas hoy.
- Desglose punta/fuera marzo: monitoreo 37.17/209.79 vs factura 53.82/172.15
  → el HP monitoreado subestima 31% vs factura (definición de punta de Kallpa
  puede diferir de 18-22 lun-vie) → pendiente confirmar tarifario.

## Sesión 8 — 2026-08-05: Criterio del cliente para total Oechsle + salto de contador

El cliente aclaró el criterio de cálculo del consumo de Oechsle Salaverry:
**SOLO los 2 tableros "Llave general TG-TR1" (punto 75) + "TG-TR2 (TF-AA) -
HVAC" (punto 76)**. NO se suman "Red normal" (67) ni "Llave General
TGE-TR1" (69) (tienen is_main=true pero el cliente los excluye).

Resultados marzo-2026 con el criterio del cliente:
- TG-TR1: 63.38 MWh (serie diaria limpia 1,900-2,300 kWh/día) ✓
- TG-TR2: 120.97 MWh con MAX−MIN mensual / 56.5 MWh sin el salto
- Total: **184.35 MWh** vs factura **225.96 MWh** → −18.4%
- Si se excluye el salto del 23-mar: 119.9 MWh → −47% (aún peor)

⚠️ **Salto de contador TG-TR2 (punto 76) el 23-mar-2026**: el EPpos pasó de
59,144 (22-mar 23:59) a ~122,623 (23-mar 23:59) = +62,979 kWh en un día
(único día >4,000 kWh en feb-jul 2026). La serie es continua (no hubo
reset). Hipótesis: el equipo corrigió energía no contabilizada; o falla del
medidor. TG-TR2 rinde 89.5-97.4 MWh/mes en abr-jul (≈3,000 kWh/día) vs
~1,500 kWh/día en los primeros 22 días de marzo.

Implicancias: con 2 tableros el monitoreo queda ~18-33% BAJO la factura
(mientras que con 4 llaves daba ~95-103%). Pendiente: (a) qué miden los
puntos 67/69; (b) periodo real de lectura del BRG; (c) validar el salto del
23-mar contra el medidor de Kallpa. Criterio del cliente registrado en
AGENTS.md y prompts.py.

### Forense del salto TG-TR2 (23-mar-2026 16:11, punto 76) — sesión 8 cont.
- Salto = UNA sola lectura: 60,091.4 → 122,590.5 (+62,499.1 kWh) entre
  16:10 y 16:11. No hubo valores intermedios ni pico sostenido.
- Post-salto el contador NUNCA volvió al valor previo: siguió desde
  122,590.5 y subió normal (3.3-4.1 kWh/min) hasta 147,604.6 (31-mar).
- Ritmo de conteo cambió en el MISMO minuto: ~2.0 kWh/min antes del salto
  (≈1,500 kWh/día) → ~3.3-4.1 kWh/min después (≈3,000 kWh/día, coherente
  con abr-jul 89-97 MWh/mes).
- Interpretación: reconfiguración/corrección del equipo en ese instante.
  Marzo de TG-TR2 = 58 MWh (ritmo viejo) o ~93 MWh (ritmo nuevo); solo el
  medidor de Kallpa lo dirime.

## Sesión 9 — 2026-08-05: Salto TG-TR2 RESUELTO (corrección de subconteo al 50%)

### Objetivo
Determinar si el salto del 23-mar se relaciona con el equipo (Acrel ADW300,
dispositivo 71) comparando el comportamiento de toda la flota.

### Evidencia decisiva — llave vs subcircuitos, día a día (marzo)
| Día | Llave TG-TR2 (76) | Σ subcircuitos (77-84) | Relación |
|---|---|---|---|
| 20-mar | 1,316 kWh | 2,679 kWh | 49% |
| 21-mar | 1,404 | 2,846 | 49% |
| 22-mar | 1,502 | 3,030 | 50% |
| 23-mar | **64,479** (salto) | 2,944 | — |
| 24-mar | 2,624 | 2,617 | **100%** |
| 25-mar | 2,696 | 2,683 | 100% |
| 26-mar | 3,197 | 3,162 | 101% |
| 27-mar | 3,079 | 3,045 | 101% |
| 28-29-mar | ~3,085 | ~3,048 | 101% |

El dispositivo 71 contaba a la **mitad exacta** desde su instalación (12-feb);
el 23-mar 16:11 fue corregido: el salto +62,499 = energía no contada acumulada
(~45 días × ~1,390/día) y desde entonces llave = subcircuitos (100%).

### Descarte de problema generalizado del ADW300
- **TG-TR1 (punto 75, ADW300)**: sano. Rinde estable 106-119% de sus
  subcircuitos (ADW210 + ADW300) desde 14-feb hasta hoy — sin cambios de
  ritmo. El subconteo NO es del modelo ni de todos los puntos Oechsle: es del
  dispositivo 71 específico (configuración/CT mal puesta).
- **Escaneo completo feb-jul 2026** (Oechsle + Sanna + Madam Tusan, días >5×
  promedio y >2,000 kWh): solo UN día anómalo en toda la flota = el 23-mar.
- **"Duplicados" feb→mar en los demás puntos Oechsle = artefacto de febrero
  parcial**: EPpos arrancó progresivamente el 07 (puntos 69, 67), 12 (76, 68),
  13 (77-84) y 14-feb (75). Los totales mensuales de febrero cubren menos
  días → aparentan subir ~1.3-1.5× en marzo sin que nada cambie (TG-TR1
  verificado: ~2,050 kWh/día constante desde 14-feb).

### Conclusión y ajuste al dato
- El salto NO es un defecto del ADW300 ni una falla de datos: es la
  **corrección legítima de un subconteo del 50%** del dispositivo 71.
- **Marzo real de TG-TR2 ≈ 90 MWh** (suma de subcircuitos); el registrado
  (121 MWh) incluye la corrección que pertenece a feb-12 → 23-mar.
- Impacto en total Oechsle (75+76) marzo: ~153 MWh reales vs 184 registrados
  vs 225.96 facturados (aún −32% con la corrección aplicada → persiste la
  brecha de los puntos 67/69 y el periodo del BRG, pendientes).
- Acción sugerida al cliente: revisar in situ CTs/configuración del
  dispositivo 71 (dev id 71, ADW300 TG-TR2) para confirmar; aplicar la misma
  auditoría llave-vs-subcircuitos a otras sedes como chequeo de salud.
- Actualizados: AGENTS.md, src/prompts.py (aviso del salto → explicación).

## Sesión 10 — 2026-08-12: Voz con ElevenLabs (push-to-talk, todo local)

### Objetivo
Hablar con ZEIA por voz desde la app web local. Se dejó de lado (por ahora) el
plan de producción agente↔Django↔frontend; todo corre en local (:8000).

### Decisiones
- ZEIA sigue siendo el cerebro; ElevenLabs solo STT + TTS (NO "ElevenLabs
  Agents", que reemplazaría al agente con su propio LLM).
- Push-to-talk (pointerdown/up), no toggle. Auto-stop a los 60 s (costos).
- STT: `scribe_v1` con `language_code=es`. TTS: `eleven_flash_v2_5` (baja
  latencia, buena calidad en español).
- La voz NO lee markdown: `clean_for_speech()` quita tablas/`**`/emojis y
  trunca a 1,500 chars ("…la respuesta completa está en pantalla").
- Sin dependencias nuevas: httpx (ya venía con openai). `/api/voice/transcribe`
  recibe el audio crudo en el body → no hace falta python-multipart.
- Barge-in: presionar el micrófono detiene el audio que ZEIA está hablando.
- Respuesta hablada SOLO si la pregunta vino del micrófono (flag viaVoice).

### Implementación
- `src/elevenlabs.py`: transcribe(), synthesize(), clean_for_speech(),
  ElevenLabsError. `src/config.py`: ELEVENLABS_API_KEY / VOICE_ID / STT_MODEL /
  TTS_MODEL (voz por defecto 21m00Tcm4TlvDq8ikWAM).
- `webapp.py`: GET /api/voice/status, POST /api/voice/transcribe (máx 10 MB),
  POST /api/voice/tts → audio/mpeg. Sin API key → 502 claro y la UI
  deshabilita el botón 🎙.
- `web/static/index.html`: botón 🎙 push-to-talk (webm/opus vía MediaRecorder),
  indicador "transcribiendo", auto-envío del texto transcrito al flujo de chat.

### Verificación
- Sintaxis OK. Servidor local: status → {"enabled": false}; transcribe/tts sin
  key → 502 "Falta ELEVENLABS_API_KEY en .env"; index 200.
- Con key real (sk_…): status → {"enabled": true}.
- **STT verificado** ✔: wav de prueba → 200 (Scribe describió el tono como
  "[tono de llamada]"). Auth + permiso speech_to_text OK.
- **TTS bloqueado por plan free**: 402 "Free users cannot use library voices
  via the API" con la voz por defecto (Rachel). Solución: crear una voz propia
  con Voice Design (gratis, usable por API) y poner su ID en
  ELEVENLABS_VOICE_ID, o pasar a plan Starter.
- Notas: la primera key que se pegó era el key ID (64 hex), no la key (sk_…).
  La key real se creó con permisos restringidos (sin user_read/voices_read) —
  suficiente para la app (STT/TTS), solo no se puede listar voces por API.
- Detalle Windows: curl con acentos en -d rompe el JSON (cp1252); usar ASCII
  en pruebas de shell. El navegador manda UTF-8 correcto.
- **RESUELTO (mismo día)**: el usuario creó una voz con Voice Design
  (ELEVENLABS_VOICE_ID=fITBqw6gMUKNgN9nO0aF). Circuito completo verificado:
  TTS → 120 KB audio/mpeg (200) → ese MP3 → STT → 200 con el texto correcto
  (Scribe transcribió "Zeia" como "Seya" — quirk cosmético, sin importancia).
- Todo local listo: navegador → 🎙 push-to-talk → STT → ZEIA → TTS → audio.

### Ajuste UX (mismo día): asistente de voz conversacional
- **La voz ya NO lee la respuesta textual**: nuevo `src/speech.py` +
  `POST /api/voice/speak`. Un LLM rápido (VOICE_SUMMARY_MODEL =
  gemini-2.5-flash vía OpenRouter) convierte la respuesta en 1-3 frases
  habladas con las cifras clave + insight accionable; fallback =
  clean_for_speech. Prompt con regla anti-alteración de cifras/unidades
  (verificado: 1,691.2 kWh / S/ 262.1 / 310 kWh 00:00-05:00 exactos).
  Tablas/gráficos quedan SOLO en pantalla.
- **Push-to-talk → modo conversación (VAD)**: el botón 🎙/⏹ activa/desactiva.
  Web Audio API mide energía RMS: >0.02 = habla (empieza a grabar), 1.3 s de
  silencio = fin de turno → transcribe → ZEIA → habla. getUserMedia con
  echoCancellation + noiseSuppression; barge-in (hablar corta el TTS);
  descarta audios <1,500 bytes; indicador de estado (Escuchando/Te escucho/
  Transcribiendo/ZEIA está pensando). Constantes en index.html: VAD_THRESHOLD,
  SILENCE_MS, MIN_AUDIO_BYTES (ajustables según ambiente ruidoso).
- Nota: Scribe transcribe "Sanna" como "Sana/Seya" a veces (cosmético; el
  audio TTS sí dice "Sanna").

---

## Sesión 11 — 2026-08-17: Cambio a DB local + prueba de DeepSeek V4 Pro

### Infraestructura: producción → DB local
- `.env` ahora apunta a PostgreSQL local: `DB_HOST=127.0.0.1`,
  `DB_PORT=5432`, `DB_NAME=energy`, usuario `postgres` (petición del usuario:
  dejar de usar producción).
- Nuevo flag `USE_SSH_TUNNEL` (.env → `src/config.py`): con `false` el código
  NUNCA abre el túnel SSH hacia producción; si el puerto local no responde,
  `tunnel.ensure_tunnel()` lanza error claro. Default de `DB_PORT` ahora 5432.
- Verificado con `scripts/test_connection.py`: PostgreSQL 16.14 local,
  `db=energy`, usuario `postgres`, `read_only=on`, esquema `public`. Web
  reiniciada en :8000 con la nueva configuración.
- Para volver a producción: `DB_PORT=55432` + `USE_SSH_TUNNEL=true` en `.env`.

### Prueba de DeepSeek V4 Pro (q1, q2, q7, q10, contra la DB local)
- `deepseek/deepseek-v4-pro`: 4/4 ok, 110.9s medio, $0.325 total
  (eval/results/run_20260817_151421.json).
- `deepseek/deepseek-v4-pro-0813`: 4/4 ok, 103.4s medio, $0.267 total
  (run_20260817_152246.json) — más rápido y barato: menos queries en q7
  (9 vs 12) y q10 (17 vs 26).
- Calidad verificada: q1 correcto (6 empresas, 84 puntos); q2 usa EPpos
  max−min por tablero; q7/q10 honestos ante datos faltantes: "sin datos" para
  julio-2026 en Madam Tusan (datos hasta 14-may) y Burger King (hasta 11-abr),
  usando marzo como referencia en BK — sin alucinar.
- **Candidato para el selector de la web** como opción de analítica pesada
  (alternativa a gpt-4.1-mini). Contra v3.2: misma solidez sin su lentitud
  extrema (v3.2: hasta 8 min/pregunta).

---

## Sesión 12 — 2026-08-18: Backup completo de producción → Postgres local

### Backup
- Dump completo de producción (22 GB → 1.2 GB comprimido, `pg_dump -Fc`) vía
  túnel SSH: `backups/energy_prod_20260818_123052.dump` (+ `.sha256`).
  Incluye `historical_readinghistory` (credenciales de dispositivos: guardar
  con cuidado).

### Instalación y restore en local
- Servidor NO estaba instalado (solo clientes libpq 18): `brew install
  postgresql@16` (mismo major que producción). Servicio: `brew services start
  postgresql@16`.
- Rol `postgres` (superuser, password = .env) + base `energy` creados.
- Restore con el pg_restore 18 de libpq (el de PG 16 rechaza el formato 1.16
  del dump: "unsupported version 1.16 in file header").
- Errores del restore (120) benignos: GRANTs a roles de producción
  (`bkamiche`, `energy_user` — creados localmente) + GUC `transaction_timeout`
  inexistente en PG 16.
- Verificado: 64 tablas, 9,235,700 lecturas, 77 FKs, secuencias sincronizadas,
  20 GB.

### Configuración actual
- `.env` → local: `DB_PORT=5432` + `USE_SSH_TUNNEL=false`. Verificado con
  `test_connection.py` (PostgreSQL 16.15 Homebrew). Para volver a producción:
  `DB_PORT=55432` + `USE_SSH_TUNNEL=true`.

---

## Sesión 13b — 2026-08-20: Asistente multi-base (energía + ambiental) para clientes

### Objetivo
Producto: agente IA que lee las bases de los clientes y responde cualquier
pregunta, complementando los módulos actuales. Dos bases independientes en un
solo proyecto: `energia` (energy, consumo eléctrico) y `ambiental`
(valhalladb, monitoreo de interiores — Sanna San Borja).

### Hallazgos de valhalladb (base ambiental)
- 50 tablas, esquema `public`, Django. Jerarquía ocupacional:
  enterprise_enterprise → enterprise_headquarters → enterprise_room →
  equipments_device (AM 103/AM 107/TS-201-TH) → equipments_indicatordevice →
  readings_reading (~15.6M, **1 lectura cada 5 min** por indicador, value TEXT).
- Módulo por "puntos" (`readings_readingambiental`): sin lecturas desde
  dic-2024 (885 en total) → respuestas "sin datos" + ofrecer rango 2024.
- Cliente activo: **Sanna, San Borja** (13 salas; sensores 145-152, 181, 182,
  184-186). Empresa se llama 'Sanna' (no 'SANNA'): el modelo falló con
  igualdad exacta → reforzar prompt con nombres exactos e ids.
- Sensible: account_user, authtoken_token, django_session,
  historical_*history.data (credenciales de dispositivos).
- **Misma infraestructura que energía**: ambas en 172.31.29.136:5432, pero
  pg_hba restringe `energy` desde el host ambiental → 2 claves/bastiones.
- Contraseña ambiental: `Zeia@2026.` (con punto final; la del manual ya
  estaba rotada). El puerto del manual (5435) sí aplica al túnel de casa.

### Arquitectura multi-base implementada
- `.env` con prefijos `ENERGIA_DB_*` / `AMBIENTAL_DB_*` (+ SSH por base).
  Alias retrocompatibles `DB_*` = energía. `.env.example` como plantilla.
- `config.py`: DBConfig por base; `db.py`/`tunnel.py`: engines y túneles por
  base; `tools.py`: dispatch con base; `agent.py`: EnergyAgent(base=...),
  **inyecta la fecha de hoy (Lima)** al prompt (el modelo usaba 2024);
  `prompts.py`: SYSTEM_PROMPT_AMBIENTAL completo (esquema, nombres exactos,
  unidades/umbrales, cadencia 5 min, análisis de huecos).
- `cli.py --base ambiental`; webapp: /api/chat acepta `base`, selector de
  módulo en el header, sesiones separadas por base, sugerencias por módulo.
- `scripts/sync_db.sh <energia|ambiental>` (dump/restore/verifica por base).
- `docs/producto_cliente.md`: especificaciones y guía de arranque (trabajo:
  127.0.0.1 5432/5433; casa: túneles).

### Pruebas (agente ambiental, en casa vía túnel 5435)
- "Salas y sensores" → 4 iteraciones, 2 queries, respuesta correcta.
- "Promedio CO2 por día Zona Roja 12-18 ago" → tabla correcta (616-651 ppm) ✔
  (tras inyectar fecha y nombres exactos).
- Perfil horario de temperatura → gráfico line ✔. Webapp responde en ambas
  bases (Sanna ayer energía: 1.3 GWh + 💡).
- Pendiente en el trabajo: escribir credenciales reales en `.env`, restaurar
  backups en 5432/5433 (sync --restore-only), demo.
## Sesión 14 — 2026-08-21: Reporte de huecos (agosto 2026)

### Contexto
- `.env` reestructurado con prefijos `ENERGIA_*` / `AMBIENTAL_*` (multi-base
  en curso según `docs/producto_cliente.md`) pero `config.py` aún leía los
  nombres viejos (`DB_*`) → todo el proyecto había quedado sin poder correr.
  Fix mínimo retrocompatible: `config.py` lee `ENERGIA_*` con fallback a
  `DB_*` (SSH vars con `SSH_*_ENERGIA`). No se implementó el `base=` todavía.

### Análisis ejecutado
- `scripts/analisis_huecos.py` (pipeline de la sesión del commit e8c574a):
  agosto 2026, huecos > 2 min, eventos con pausa ≤ 10 min.
- Resultados: 7,854 huecos → 4,343 eventos; 31 puntos activos;
  ~713.7k lecturas; **11,384 faltantes (~1.6 %)**. Oechsle Salaverry 186.6 h
  sin datos (6,545 lect.) / Sanna San Borja 144.2 h (4,839 lect.).
- **Peor punto**: Chiller 2 (tablero HVAC TG-TR2 de Oechsle, uno de los 2
  puntos del total de sede): 46.7 h en 33 eventos, con caídas de 10–13 h
  (15–17-ago). Patrón intermitente (huecos máx 7–9 min, no corte seco).
- Dato de cobertura: DB local termina en 17-ago 14:33 Lima → días 18–20 sin
  lecturas (sincronización pendiente).
- Reporte: `docs/reporte_huecos_agosto_2026.md`; CSVs en `analisis/`; tablas
  `analisis_huecos`/`analisis_eventos` en DB local; dashboard en `:8000/gaps`.

---

## Sesión 13 — 2026-08-19: Conexión Valhalla mediante SSH

### Conectividad
- `.env` configurado para `valhallaprod.pem`, usuario `ubuntu` y el host EC2
  de Valhalla; el túnel local expone `127.0.0.1:5435` hacia
  `172.31.29.136:5432`.
- La llave autentica correctamente y el túnel abre el puerto local.
- La contraseña mostrada en el instructivo estaba rotada. Se usó la
  credencial vigente ya disponible en el entorno, sin registrar secretos en
  esta bitácora.
- Verificado con `scripts/test_connection.py`: PostgreSQL 16.14,
  `db=valhalladb`, usuario `postgres`, `read_only=on`, esquema `public`.

---

## Sesión 15 — 2026-08-21: Análisis de huecos diario (cobertura por día/punto)

### Contexto
- Petición del usuario: análisis de huecos sobre la DB local. Complementa el
  pipeline fino de la sesión 14 (minutos, `reporte_huecos_agosto_2026.md`);
  este es a nivel de días completos y detecta caídas de días enteros.

### Herramienta
- `scripts/analisis_huecos.py` (reusable, `--intraday` opcional): inventario
  jerárquico + conteos diarios por punto (`GROUP BY point, dia Lima`) +
  episodios de hueco (<10 lect/día) + días parciales (<1300) + staleness.
- **Bug detectado y corregido**: el `GROUP BY` solo devuelve días CON filas;
  inicializar `d.get(dia, 0)` sobre el calendario completo [min, max] es
  obligatorio o los días sin datos nunca cuentan como hueco.

### Hallazgos clave (datos hasta 17-ago-2026, dump 18-ago)
- 85 puntos; 36 con lecturas (31 activos: 18 Oechsle + 13 Sanna).
- **49 puntos sin UNA lectura** — TODOS bajo dispositivos "Device
  falso/false" (circuitos no instrumentados / de prueba): no son huecos.
  Excl. TestCorp_Email. Oechsle 22 sin lecturas, Pizza Hut 13, Sanna 13.
- **Burger King**: hueco de 110 días (12-abr → 30-jul); volvió a reportar
  solo el 31-jul y está caído desde entonces (17 días). Dato sospechoso;
  hay 108 días sin datos que la sesión 14 (minutos, eventos ≤ ~10 min de
  pausa) no podía ver.
- **Pizza Hut**: hueco de 8 días (11→18-abr); murió definitivo 20-abr.
- **Madam Tusan**: 4 episodios (1×1 día + 3×2 días) en abr-may; vida corta
  (20-abr→14-may); en 4 de sus días con datos la cobertura fue <50%.
- **Sanna**: hueco 5 días en cuarto de bombas (26-feb→2-mar); 4 días en
  pinzas TGA (10→13-dic 2025, arranque); **caída global de ~3.5 días
  (3-may 04:40 → 6-may 16:08 Lima)** en TODOS los puntos (evento externo,
  probable corte de planta + problema de gateway). Días completos
  "perdidos": 4-5 may (+ bordes parciales).
- **Oechsle**: 0 huecos de día completo en 187-192 días — el más limpio.
  Huecos intra-día comunes a los 4 puntos principales (67/69/75/76):
  13-feb 16 h (arranque), 24-jun 2.4 h, 15-jul 4.3 h, 16-jul 2.1 h
  (cortes de planta, caen a la vez → la falla es de la energía, no del dispositivo).
- Cobertura: mediana 1,393 lect/día/punto (97% del teórico 1440).
- **Dashboard web** (petición del usuario: informe siempre en web):
  `scripts/analisis_huecos.py --export` escribe `analisis/cobertura_diaria.csv` +
  `analisis/cobertura_resumen.json`; la app los sirve en `/api/cobertura[/resumen]`
  y `web/static/gaps.html` ahora tiene 3 pestañas: **Cobertura diaria** (KPI,
  heatmap echarts completo/parcial/hueco por punto×día, tabla por punto con
  stale badge), Eventos agrupados y Huecos individuales (minutos, sesión 14).
  Flujo: regenerar datos (`--export`) → `python webapp.py` → `http://localhost:8000/gaps`.
- **Bug de `display` (reportado por el usuario, página en blanco)**: el JS
  "mostraba" las secciones con `style.display = ""` pero el CSS las tiene con
  `display:none` en la hoja de estilos → cadena vacía NO anula la regla y
  quedaban ocultas aunque todo se renderizaba (DOM completo, canvas dibujados).
  Fix: `display:"block"/"none"` explícito en `cambiarTab`. Verificado con
  Playwright (Chromium headless instalado en el venv para QA del dashboard).
- **Tab "Minuto a minuto"**: misma pestaña `/gaps`. `GET /api/lecturas
  ?puntos=76,75&desde=../hasta=..` (≤3 puntos, ≤10 días) devuelve los minutos
  (hora Lima) con lecturas vía índice `reading_mp_created_at_idx` (0.15 s por
  punto-día). El navegador arma el heatmap x=minuto 0–1439, fila=punto·día:
  verde=llegó, rojo=no llegó, gris=sin datos del punto, con dataZoom (zoom por
  horas) + tabla resumen por punto: lecturas, minutos faltantes, % cobertura,
  racha más larga y ventanas >5 min. Validado con el hueco de Oechsle
  15-jul-2026: franja roja 11:33–15:49 (257 min) exacta + otra de 09:41–10:41.
  Cuidado echarts: `axisLabel.interval` es por índice (Number("00:00") = NaN).
- **Horizonte de datos (17-ago 14:33)**: verificado — TODOS los puntos (Oechsle
  67/69/75/76 y Sanna 1/2/4) terminan entre 14:32:57 y 14:33:50 el 17-ago: es el
  corte de la copia local (dump 18-ago 12:30), NO una caída de medidores. El
  dashboard "Minuto a minuto" ahora recibe `meta` por punto (ultimo_dia,
  ultimo_min_idx) y pinta gris claro los minutos posteriores a la última lectura
  ("Fin de la copia local"), dejando rojo solo las faltas reales dentro del
  horizonte. /api/lecturas devuelve meta con el horizonte de cada punto.

---

## Sesión 16 — 2026-08-21: Dashboard de huecos AMBIENTAL (valhalladb, :8002)

### Contexto
- Cliente ambiental corre en `:8002` (`webapp_ambiental.py`, PORT=8002). Ya había
  pipeline (`scripts/analisis_huecos_ambiental.py`, sesión previa) con resumen por
  combo (sensor×sala / sensor×punto) pero solo reporte HTML básico.
- Se replicó el dashboard completo de energía para ambiental.

### Datos clave (valhalladb)
- 50 tablas; `readings_reading` 15.7M filas **vivas hasta HOY** (2023-01→2026-08-21);
  `readings_readingambiental` solo 885 filas (nov-dic 2024, pruebas) → el track real
  es SALAS. Índice `(indicator_device_id, created_at)` permite consultas en vivo (~0.2s).
- Cadencia real: **12 lecturas/hora = 1 cada 5 min → ~288/día** (bucket adaptativo).
- Inventario: **269 combos** (254 con datos, 15 sin); 19,142 días-hueco; mayor hueco
  377 días; sedes con nombre literal "true"/"false" (data de prueba en
  `enterprise_headquarters`). Combos Pisco-ICA/Aceros Arequipa con activado=False
  pero con histórico.

### Entregado
- Pipeline extendido: `--export` también escribe `analisis/cobertura_diaria_ambiental_{
  salas,puntos}.json` (estado día×combo contra el RITMO del sensor: completo ≥90%
  de su mediana, parcial ≥10%, hueco <).
- `webapp_ambiental.py` reescrito: `/gaps` (dashboard nuevo), `/reporte` (viejo),
  `/api/ambiental/cobertura/{resumen,diaria}?track=`, `/api/ambiental/lecturas?
  track=&combos=&desde=&hasta=` (en vivo, ≤40 combos, ≤10 días, meta horizonte),
  compat `/api/ambiental/huecos/{salas,puntos}`, estáticos montados.
- `web/static/ambiental_gaps.html`: 3 pestañas (Cobertura diaria KPI+heatmap+tabla,
  Lectura a lectura con bucket adaptativo + horizonte + tabla ventanas >15 min,
  Episodios) + selector Fuente Salas/Puntos + auto-carga + actualizando-feedback +
  comparar-todos.
- Verificado con Playwright: cobertura 269 filas OK; minuto-a-minuto CO2 Sala técnica
  20-ago = 288/288 (100%, todo verde, último dato 23:57, 0.19s); episodios 1079/19142.
- Ajuste visual (reportado por usuario): paleta oscura invisible sobre fondo
  oscuro + celdas sub-pixel en rango de 3.5 años → colores brillantes
  (#10b981/#f59e0b/#ef4444), borderWidth 0.2 y dataZoom (inside+slider) en el
  heatmap de cobertura ambiental; misma paleta aplicada a lectura-a-lectura.

## Sesión  — Formato CARD en el chat (respuestas breves + más gráficos)

### Pedido del cliente
- No: respuestas con demasiada información (tabla completa + sección 💡 larga +
  menú "¿Qué quieres hacer?"). Sí: descripción breve de la card + más gráficas.

### Cambios
- src/prompts.py: formato CARD (título → resumen 2-4 líneas → gráficos); el
  detalle vive en render_chart; top 1-2 ítems mencionados en texto; 💡 = máx 1
  línea; PERSONA_GERENTE sin menú "¿Qué quieres hacer?"; recordatorios finales
  (energía y ambiental) alineados al nuevo formato.
- src/tools.py: descripción de render_chart "máx 6" y mensaje de vuelta del
  tool en formato CARD (sin enlaces ni mención de la herramienta).
- src/agent.py: tope de gráficos por respuesta 3 → 6.
- web/static/index.html: stripChartLinks() elimina links `chart://` y
  "(render_chart)" del texto (chat y cards).

### Verificado
- Pregunta "¿Qué salas tuvieron CO2 > 1000 ppm este mes?" (ambiente, gerente):
  respuesta = título + 2 frases con top-2 + 1 bar chart (antes: tabla 7 filas +
  párrafos + 💡 3 bullets + menú). Sin links rotos.

### Ajuste visual + concision v2 (dashboard)
- Grilla: #grid sin max-width, cards ocupan 100% del ancho; .wide → span 12
  (gráficos a fila completa; antes span 8 dejaba hueco a la derecha).
- prompts.py: energía y ambiental → CARD título + 1-2 frases + gráficos;
  prohibido preguntas de seguimiento ("¿Te gustaría...?"), tablas, listas,
  y filtrar argumentos de herramientas (chart_type=...) en el texto.
- index.html: stripChartLinks también limpia líneas con chart_type=.
- Verificado (energía, gerente): "demanda máxima y P95" → 2 frases + 2 bar charts.

### Regla inventario vs métrica (prompt)
- Listados (qué tableros/salas/puntos tiene X) → 1-2 frases + TABLA COMPACTA;
  cifras/rankings/series → gráfico. Nunca enumerar en texto corrido ni bullets.
- Aplicado a energía y ambiental (recordatorios finales + sección CARD).
- Verificado: "¿Cuáles son los tableros de Oechsle?" → frase + tabla 4 filas.

## Sesión 07-sep-2026 — Sync ambiental full (5433)

### Pedido
- Usuario con Postgres local ya escuchando en 5433 (valhalladb): actualizar
  toda la data hasta hoy (dump + sync, con túnel SSH).

### Ejecutado
- `bash scripts/sync_db.sh ambiental full`: túnel SSH propio en 5435 hacia
  `ubuntu@ec2-44-206-41-101...` → dump `backups/ambiental/
  ambiental_prod_20260907_095631.dump` → DROP SCHEMA public + restore en
  127.0.0.1:5433 → túnel cerrado.
- Local estaba en 31-ago-2026 11:42 Lima; quedó en 07-sep-2026 09:56 Lima
  (15,888,976 lecturas). Prod al momento del verify: 15,889,026 (+50 filas
  que ingresaron en vivo durante la ventana dump/restore; última prod
  10:04 Lima vs local 09:56 Lima, ~8 min de diferencia — normal en DB viva).
- Dumps viejos: se conservan los últimos 3 (31-ago, 04-sep, 07-sep).

## Sesión 07-sep-2026 — Sync energía full (5432)

### Pedido
- Usuario con Postgres local ya escuchando en 5432 (energy): actualizar toda
  la data hasta hoy (dump + sync, con túnel SSH).

### Ejecutado
- 1er intento de `scripts/sync_db.sh energia full` FALLÓ a mitad del dump de
  las tablas grandes (readings_reading/historical_readinghistory): el túnel
  SSH se cayó con "SSL SYSCALL error: EOF detected" (~829 MB descartados).
  Causa: SSH sin keepalives sobre red casera → NAT corta la conexión idle/larga.
- 2do intento OK: túnel manual reforzado ANTES del script
  (`ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=6 -o TCPKeepAlive=yes`
  en 55432; el script lo detecta abierto y lo reusa) → dump
  `backups/energia/energia_prod_20260907_101221.dump` (1.4 GB, -Fd -j 4) →
  DROP SCHEMA public + restore en 127.0.0.1:5432.
- Local estaba en backup del 02-sep (start.log); quedó en 07-sep-2026 10:12
  Lima (10,089,434 lecturas). Prod al momento del verify: 10,090,283 (+849
  filas en vivo durante la ventana dump/restore; última prod 10:39 Lima vs
  local 10:12 Lima — normal en DB viva, NO es error de sync).
- Túnel manual cerrado a mano tras el sync (el script no cierra túneles que
  reusó). Dump del 31-ago eliminado (conserva 3 últimos).
- LECCIÓN para próximos syncs: si el dump muere con EOF/SSL SYSCALL, abrir
  túnel con keepalives y reintentar; no bajar -j.

## Sesión 14-sep-2026 — Sync ambiental full (5433)

### Pedido
- Levantar los Postgres locales (energía 5432 / ambiental 5433) y correr
  dump + restore SOLO de la base ambiental.

### Ejecutado
- `pg_ctl -D C:/pgsql16/data` (energía, 5432) y `-D C:/pgsql16/data-valhalla`
  (ambiental, 5433) → ambos servidores arriba.
- Túnel manual con keepalives en 5435 (lección del sync energía 07-sep) →
  `bash scripts/sync_db.sh ambiental full` (reusó el túnel) → dump
  `backups/ambiental/ambiental_prod_20260914_094112.dump` (232 MB, -Fd -j 4)
  → DROP SCHEMA public + restore en 127.0.0.1:5433.
- Verify: prod=15,957,607 vs local=15,957,564 (43 filas de diferencia, normal
  en DB viva); última lectura local 09:41:08 -05 vs prod 09:46:36 -05 (~5 min).
- Dump viejo 31-ago eliminado (quedan 04-sep, 07-sep, 14-sep). Túnel cerrado
  a mano: `netstat` mostraba PID stale 1540 (inexistente); el ssh real era
  PID 12412 (visible en `Get-CimInstance Win32_Process`) → `taskkill //PID`. 
   OJO para próximos cierres de túnel en Windows: verificar el PID real, no
   confiar en el de netstat.

## Sesión 14-sep-2026 — Reinicio de apps + /gaps regenerados

### Pedido
- Reiniciar ambas apps web (energía :8000, ambiental :8002) para que tomen
  la data de hoy, y que los /gaps muestren hasta la última fecha (se veían
  solo hasta 31-ago porque los exports eran del 02-sep, con DBs viejas).

### Ejecutado
- Matados los árboles viejos (webapp.py 20208→18640; webapp_ambiental.py
  14588→8108 en :8001 y 10268→21180 en :8002 — había DOS instancias
  ambiental). Levantadas limpias: `webapp.py` (:8000) y
  `PORT=8002 webapp_ambiental.py` (:8002). Logs en `logs/`.
- Regenerados: `scripts/analisis_huecos.py --export` → cobertura_diaria.csv
  (7,617 filas) + cobertura_resumen.json (86 puntos); y
  `scripts/analisis_huecos_ambiental.py --export` → huecos/cobertura
  ambiental salas+puntos (72,092 días-combo).
- Verificado por API: ambos /gaps con global_max_day 2026-09-14. Las apps
  leen los JSON por request → no hizo falta reiniciar tras el export.
  (Si el navegador muestra datos viejos: hard refresh Ctrl+F5.)

## Sesión 14-sep-2026 — Tooltip cobertura diaria con cifras

### Pedido
- En el heatmap de Cobertura diaria (energía) el tooltip solo decía
  "Completo/Parcial/Hueco": mostrar lecturas esperadas vs recibidas y %.

### Ejecutado
- `web/static/gaps.html` (`pintarHeatmap`): nuevo mapa `detalle`
  (key|día → {n: lecturas, p: pct} del CSV) y tooltip ampliado a
  `"<fecha> <punto> <estado> · N de 1440 lecturas (P%)"` (días sin fila =
  "0 de 1440 (0%)"). Los campos ya venían en `/api/cobertura`, solo se
  mostraban. Sintaxis verificada con `node --check`; sin reinicio
  (StaticFiles sirve de disco; el navegador necesita Ctrl+F5).

## Sesión 14-sep-2026 — Reporte semanal de cobertura (para jefes)

### Pedido
- Reporte de cobertura 07–13 sep (energía + ambiental) como los heatmaps de
  /gaps pero limpio, con fechas "Lunes xx de septiembre" y solo puntos
  relevantes. Formato elegido: página web en la app.

### Ejecutado
- `GET /api/reporte-semanal?desde=&hasta=` (default = última semana completa
  lun–dom) + `GET /reporte-semanal` en `webapp.py` (:8000, lee los CSV/JSON
  de `analisis/` de ambos módulos; helpers de fechas en español).
- `web/static/reporte_semanal.html`: tema claro imprimible, KPIs, mini-
  heatmaps solo con puntos/salas con incidencias (energía 6×7, ambiental
  2×7 agregando por sala con peor indicador del día), tooltips con cifras,
  incidencias en español ("sin datos lunes 07 a viernes 11 de septiembre")
  y <details> con puntos sin datos en la semana. Enlace desde /gaps.
- Semana 07–13 sep: energía 95.0% (26/32 al 100%, 0 días-hueco), ambiental
  89.0% días-sala (11/13 limpias; Sala de Operaciones 2 y Zona Azul con
  huecos). Verificado por API + `node --check`; app reiniciada 2 veces
  (los endpoints nuevos requieren restart; el HTML estático no).
- Bug: heatmap en blanco porque `echarts.init` corría antes de fijar la
  altura del contenedor (medía 0px). Fix: fijar `el.style.height` ANTES del
  init. Lección: en páginas nuevas con ECharts, altura primero, init después.
- Pedido: ver TODAS las filas (no solo incidencias). API ahora devuelve
  todas con flag `con_incidencia` (energía 32, ambiental 13 salas agregadas);
  frontend con casilla "Mostrar solo … con incidencias" (default: todo) +
  helper `pintar()` (vacío/dispose/re-render) y `chart.resize()` tras
  setOption para re-renderes con distinta altura.
- Aclaración importante: el /gaps ambiental pinta una fila POR COMBO
  (sala×indicador): las 6 filas rojas/ámbar son los 3 indicadores de Sala de
  Operaciones 2 + los 3 de Zona Azul (2 salas). El reporte agrupaba por sala
  y parecía ocultar info → heatmap ambiental cambiado a filas por combo
  (38 total, 6 con incidencia), igual que el /gaps pero con etiquetas
  limpias. KPIs e incidencias siguen a nivel sala.

## Sesión 14-sep-2026 — Sync energía full (5432) + apps web

### Pedido
- Iniciar las apps web (energía :8000 y ambiental :8002) y luego actualizar la
  base local `energy` con dump + restore de producción (solo energía).

### Ejecutado
- Apps arriba: `webapp.py` en :8000 (200 OK) y `webapp_ambiental.py` con
  PORT=8002 en :8002 (307, redirect normal).
- Túnel manual reforzado con keepalives ANTES del script (misma lección del
  07-sep) en 55432 → `bash scripts/sync_db.sh energia full` lo reusó.
- Dump `backups/energia/energia_prod_20260914_095540.dump` (1.4 GB, -Fd -j 4
  -Z 5) → DROP SCHEMA public + restore en 127.0.0.1:5432.
- Verify: prod=10,396,295 vs local=10,395,523 (+772 filas en vivo durante la
  ventana dump/restore, normal); última lectura local 14-sep 09:55 Lima
  (= inicio del dump) vs prod 10:20 Lima.
- Túnel manual cerrado a mano (PID 26856, confirmado con taskkill). Dump del
  31-ago eliminado (quedan 04-sep, 07-sep, 14-sep).
- Ambas apps siguen respondiendo tras el restore (conexiones psycopg2 son
  por request, no quedan sesiones idle-in-transaction que bloqueen el DROP).

## Sesión 15-sep-2026 (casa) — Últimas lecturas + sync energía full

### Pedido
- Últimas lecturas de ambas bases; luego actualizar energía.

### Ejecutado
- Energía local estaba en 09-sep 16:20 Lima (10,188,758 filas) vs prod con
  datos hasta hoy 15-sep 08:37 (10,437,168). Ambiental en vivo hoy 15-sep
  ~08:36 (15.97M filas, 13 salas Sanna San Borja); su túnel (5433,
  valhallaprod.pem) se levantó a mano y quedó abierto.
- `bash scripts/sync_db.sh energia full` (reusó túnel manual en 55432):
  dump `backups/energia/energia_prod_20260915_083723.dump` (1.4 GB) → DROP +
  restore en 5432. Verify: prod=10,438,053 vs local=10,437,170 (dif ~883
  filas = ingesta en vivo durante la ventana, normal); última local 15-sep
  08:37 Lima (Oechsle; Sanna 08:37:08, Scotiabank Jockey Plaza 08:36:30).
  +248,412 filas nuevas vs el estado previo.
- Novedad: `Scotiabank / Jockey Plaza` reporta en vivo (no está en prompts.py);
  KFC Salaverry reaparece (hasta 11-abr-2026). BK hasta 31-jul, Madam Tusan
  14-may, Pizza Hut 20-abr.
- Bug en `sync_db.sh/cleanup_dumps`: `[ ... ] && { ...; }` como última
  sentencia del loop + `set -e` aborta el script antes de imprimir "listo"
  cuando no hay nada que borrar. Fix: cambiado a `if...fi` (verificado con
  `bash -n`). Dump/restore/verify no se vieron afectados.

## Sesión 15-sep-2026 (casa) — Reporte semanal HTML 07–13 sep

### Pedido
- Reporte en HTML de la semana pasada (lun–dom) para mostrar huecos al jefe:
  solo puntos con huecos (omitir los OK), cobertura por punto y día, ambiental
  organizado por sala×indicador, fechas como "Lunes 07 de septiembre".

### Ejecutado
- El API `GET /api/reporte-semanal` ya existía pero el frontend
  `web/static/reporte_semanal.html` no (nunca se commiteó). Creado desde cero:
  auto-contenido sin dependencias, tema claro imprimible, KPIs, heatmaps solo
  con incidencias (`con_incidencia`), ambiental agrupado por sala con subfilas
  por indicador, `puntos_sin_datos` agrupados por empresa/sede, fechas largas
  en español + tooltip con n° de lecturas. JS validado con `node --check`.
- Re-exports (los del 09-sep no cubrían 10–13): `analisis_huecos.py --export`
  (7,649 filas) + `analisis_huecos_ambiental.py --export`; webapp reiniciada.
- Semana 07–13 sep: energía 95.0% (26/32 OK; 6 puntos con días parciales, 0
  días-hueco; TF-UMAS 1 de Oechsle el peor con 5 parciales). Ambiental 89.0%
  días-sala (11/13 OK; Sala de Operaciones 2 sin datos lun 07–vie 11 + parcial
  fin de semana; Zona Azul sin datos mié 09–jue 10 + parcial vie 11).
- URL: http://localhost:8000/reporte-semanal (default = última semana completa).

## Sesión 24-sep-2026 (casa) — Sync energía + ambiental full (ambos al día)

### Pedido
- Sincronizar AMBAS bases a hoy (dump + restore). El usuario creía que ya
  estaban corriendo en algún puerto; verificado: solo energía (5432) estaba
  arriba.

### Estado inicial encontrado
- Postgres local energía 5432 corriendo (brew); clúster ambiental
  `/opt/homebrew/var/postgresql@16-ambiental` (port 5433) APAGADO y sin
  túneles SSH. Se levantó con
  `pg_ctl -D /opt/homebrew/var/postgresql@16-ambiental -l /opt/homebrew/var/log/postgresql@16-ambiental.log start`
  (queda corriendo; no tiene LaunchAgent de Homebrew).

### Ejecutado
- Túneles manuales con keepalives (lección del sync 07-sep) en 55432
  (energy.pem) y 5435 (valhallaprod.pem); `sync_db.sh` los reusó.
- Energía: local 15-sep 08:37 (10,437,170) → dump
  `backups/energia/energia_prod_20260924_091801.dump` (1.5 GB) → restore en
  5432. Local quedó 10,832,055 filas, últ. lectura 24-sep 09:17:57 -05; prod
  al verify 10,832,933 (dif 878 filas en vivo, normal).
- Ambiental: local 09-sep 17:01 (15,911,383) → dump
  `backups/ambiental/ambiental_prod_20260924_094634.dump` (260 MB) → restore
  en 5433. Local quedó 16,066,412 filas, últ. 24-sep 09:46:33 -05; prod
  16,066,427 (dif 15, normal).
- Nota: en `valhalladb` la tabla activa es `readings_reading` (ocupacional);
  `readings_readingambiental` sigue inactiva (885 filas, últ. dic-2024).
- Túneles cerrados a mano al final; dumps energía conserva 09/15/24-sep.

## Sesión 24-sep-2026 (casa) — Reportes semanales 07–13 y 14–20 sep

### Pedido
- Análisis de conectividad (cobertura) de energía + ambiental para jefes, de
  las semanas 07–13 sep y 14–20 sep (HTML imprimible + resumen).

### Ejecutado
- Exports regenerados con la data fresca de hoy:
  `scripts/analisis_huecos.py --export` (7,937 filas) y
  `scripts/analisis_huecos_ambiental.py --export` (72,472 días-combo); logs
  `analisis/export_{energia,ambiental}_20260924.log`.
- Webapp levantada en :8000. URLs:
  `/reporte-semanal?desde=2026-09-07&hasta=2026-09-13` y
  `?desde=2026-09-14&hasta=2026-09-20`.

### Hallazgos
- ⚡ 07–13: 95.0% (26/32 puntos OK, 0 días-hueco). TF-UMAS 1 de Oechsle
  parcial los 5 días (33–55% de su ritmo); TGE-TR1 07–08 (~71%); Tomógrafo
  09 y 11; Llave general Sanna 11; TF-UMAS 2 12; TF-VM 07.
- ⚡ 14–20: 94.7% (13/32 OK, 0 días-hueco). Evento de sede Oechsle Salaverry
  el lun 15 (~14:20–18:40, corte de ~4h20m) → 18 puntos al ~79–80%. Además:
  Chiller 1 (16 y 17, hasta 61%), TF-UMAS 1 (14–15, 44–58%), TF-UMAS 2
  (15–16, 69–79%), TGE-TR1 (18, 84%), Tomógrafo (20, 63%). Verificado que la
  cadencia normal de esos puntos es ~1,377–1,392 lecturas/día (el umbral
  completo=1,300 es correcto).
- 🌡 07–13: 89.0% (11/13 salas OK). Sala de Operaciones 2 sin datos lun 07 a
  vie 11 y parcial el fin de semana (28–55%); Zona Azul sin datos mié 09–jue
  10 y parcial vie 11 (44%).
- 🌡 14–20: 98.9% (12/13 salas OK). Sala de Operaciones 2 solo parcial el lun
  14 (42–44%); recuperada desde el mar 15.
- Contexto: Burger King (31-jul), Pizza Hut (20-abr), Madam Tusan (14-may) y
  KFC (11-abr) siguen sin medición; ~49 puntos de inventario nunca medidos
  (subcircuitos sin instalar). Se reportó como tal, sin contarlos como falla
  de conectividad de las semanas.

### Corrección del reporte (feedback del usuario)
- Problema: el jefe no entendía "huecos" ni de dónde salía el %; y ambiental
  se veía por indicador, no por sala.
- `webapp.py`: el reporte ambiental ahora devuelve `filas_salas` (una fila
  por SALA: estado del día = peor indicador, % = lecturas recibidas / lecturas
  normales de sus sensores ese día) además de los combos para el detalle;
  `dias_hueco` en ambas bases; detalle textual "datos incompletos" (ya no
  "parcial").
- `reporte_semanal.html` reescrito: título "Reporte semanal de conectividad",
  caja "¿Qué muestra este reporte?" (energía: 1,440 lecturas/día = 1/min;
  ambiental: ritmo propio del sensor; verdes ≥90%), leyenda en palabras
  (día normal / datos incompletos / sin datos), KPIs en lenguaje simple,
  tabla ambiental por sala con filas indentadas por indicador solo donde hay
  problema, tooltips con lecturas exactas y frase positiva si no hubo ningún
  día entero sin datos. JS validado con `node --check`; webapp reiniciada.
- 2º feedback: quitar las tarjetas KPI y dejar solo una frase corta
  ("se esperan 1,440 lecturas/equipo en energía y el ritmo normal de cada
  sensor en ambiental; así se mide la conectividad"). Las tablas y leyendas
  quedan como única vista.
- 3er feedback: detallar ambiental en la explicación. La frase ahora dice
  "~288 por sensor en ambiental (1 cada 5 min)" (ritmo real de Sanna San
  Borja) y los tooltips ambientales muestran "X de Y lecturas esperadas (Z%)"
  (Y = ritmo del sensor o suma de ritmos de la sala). API: `esperadas` por
  celda en `filas` y `filas_salas`.
- 4º feedback: aclarar por qué faltan salas en la tabla. Se añadió debajo
  "✅ Las otras N salas no tuvieron ninguna incidencia (por eso no aparecen en
  la tabla): <nombres>".
- 5º feedback: la misma línea para energía ("✅ Los otros 26 equipos no
  tuvieron ninguna incidencia…") debajo de la tabla de equipos.
- Deuda detectada (no corregida): en `analisis_huecos_ambiental.py/export`
  los campos del JSON salen corridos (empresa=sede, sede=sala_activa,
  lugar=sala; `r[11]` empresa sin usar). El reporte funciona porque usa esos
  mismos nombres; si algún día hay salas de varias sedes, corregir el export.

## Sesión 28-sep-2026 — Sync full de AMBAS bases (energía + ambiental)

### Pedido
- Generar dump + restore de las dos bases (energía y ambiental).

### Ejecutado
- Postgres locales caídos al inicio: `pg_ctl -D C:/pgsql16/data` (energía,
  5432) y `-D C:/pgsql16/data-valhalla` (ambiental, 5433) → ambos arriba.
- Túneles manuales con keepalives ANTES del script (lección 07-sep) en 55432
  y 5435 → `sync_db.sh {energia,ambiental} full` los reusó.
- Energía: dump `backups/energia/energia_prod_20260928_093307.dump` (1.5 GB,
  -Fd -j 4 -Z 5) → restore en 127.0.0.1:5432. Verify: prod=11,011,485 vs
  local=11,010,859 (+626 filas en vivo, normal); última local 09:33 Lima
  (= inicio del dump) vs prod 09:53 Lima.
- Ambiental: dump `backups/ambiental/ambiental_prod_20260928_095403.dump`
  (235 MB) → restore en 127.0.0.1:5433. Verify: prod=16,110,060 vs
  local=16,110,028 (+32 filas en vivo, normal); última local 09:54:14 -05
  (= inicio del dump) vs prod 09:57:38 -05.
- Túneles cerrados a mano (PIDs reales 9812/10032 vía Get-CimInstance;
  puertos 55432/5435 confirmados cerrados). Dumps viejos del 04-sep
  eliminados (quedan 07-sep, 14-sep, 28-sep por base).

## Sesión 28-sep-2026 — Reporte semanal 21–27 sep (post-sync)

### Pedido
- Crear el reporte de cobertura de la semana del 21 al 27 de septiembre.

### Ejecutado
- Exports regenerados con la data fresca: `analisis_huecos.py --export`
  (8,065 filas, 86 puntos) y `analisis_huecos_ambiental.py --export`
  (72,624 días-combo salas).
- `venv/Scripts/python.exe webapp.py` (:8000) y `GET /api/reporte-semanal
  ?desde=2026-09-21&hasta=2026-09-27`:
  energía 96.2% (28/32 puntos al 100%, 0 días-hueco; 4 con días parciales:
  Oechsle TF-UMAS 2, TG-TR2, Chiller 1 y Sanna Llave general);
  ambiental 100.0% (13/13 salas y 38/38 combos OK, sin incidencias).
- Reporte disponible en http://localhost:8000/reporte-semanal?desde=
  2026-09-21&hasta=2026-09-27 (default de la página = última semana
  completa, que es exactamente 21–27 sep).

### Pedido posterior (misma sesión): PDF + solo incidencias
- `web/static/reporte_semanal.html`: el heatmap ahora muestra SOLO
  puntos/sensores con incidencia por defecto (casilla "Mostrar también los
  puntos al 100%", unchecked); si todo está OK se muestra el mensaje verde
  sin heatmap. Botón "Descargar PDF".
- `webapp.py`: `GET /reporte-semanal/pdf?desde=&hasta=` genera el PDF con
  Chrome/Edge headless (`--headless=new --print-to-pdf`,
  `--virtual-time-budget=20000`, perfil temporal; autodetecta binario,
  override `CHROME_BIN`) y lo devuelve con Content-Disposition attachment.
  En print CSS se ocultan controles y casillas de filtro.
- Verificado con la semana 21–27: PDF 1 página, headers OK, solo los 4
  puntos con incidencia (TF-UMAS 2, TG-TR2, Chiller 1, Sanna Llave
  general), 100% de Scotiabank/Sanna fuera.

### Bug encontrado: el PDF no salía para el rango pedido
- Pedido del usuario: "tendría que salir ambiental y energía". Causa raíz:
  `reporte_semanal.html` IGNORABA `?desde=&hasta=` de la URL (los inputs
  arrancaban vacíos y `cargar()` fetcheaba sin params = última semana
  completa). El endpoint /reporte-semanal/pdf pasaba el rango en la URL,
  pero la página siempre renderizaba la semana por defecto: para 21–27
  "coincidía" y parecía funcionar; para cualquier otro rango (p.ej. 07–13
  con incidencias ambientales de Sala de Operaciones 2 y Zona Azul) el PDF
  mostraba la semana equivocada, sin el heatmap ambiental.
- Fix: leer `location.search` y prefijar `f-desde`/`f-hasta` antes de
  `cargar()`.
- Verificado: 07–13 ahora da 2 páginas, 2 imágenes (heatmaps energía +
  ambiental), 89% y las salas con incidencia; 21–27 sigue OK (1 imagen,
  96.2%, ambiental al 100%).

## Sesión 02-oct-2026 — Reporte semanal estilo "por equipo/sala y día"

### Pedido
- Replicar el reporte que el usuario usa en el trabajo (capturas de un PDF
  impreso): tablas por equipo y día (energía) y por sala/sensor y día
  (ambiental), en vez del heatmap de la sesión 28-sep.

### Ejecutado
- `web/static/reporte_semanal.html` reescrito: tablas con celdas
  verde/ámbar/rojo (día normal ≥90% / incompleto / sin datos), leyenda,
  nota verde con la lista de equipos/salas perfectos (no se listan en la
  tabla), lista de "Equipos sin ninguna lectura en la semana" agrupada por
  empresa/sede con conteo, "Detalle de las salas afectadas" y nota final
  "Ningún equipo ni sala se quedó un día entero sin enviar datos" (solo si
  no hubo días-hueco). Formato de celdas: 1 decimal sin ".0" (94.0 → 94%).
- `webapp.py`:
  - `_reporte_energia`: + `puntos_ok_lista` (etiquetas de los perfectos,
    en el orden de la tabla, para la nota verde).
  - `_reporte_ambiental`: + `salas` (fila resumen por sala: promedio de sus
    sensores por día, estado = peor del día), + `salas_ok_lista`,
    + `dias_hueco` (días en que la sala entera quedó sin datos); las
    incidencias ya no se truncan a 8 y el detalle dice "datos incompletos"
    (antes "parcial").
- Verificado con datos locales (exports del 28-sep):
  - Semana 14–20 sep reproduce las capturas: 32 puntos (19 con incidencia,
    13 perfectos) y 13 salas (1 afectada: Sala de Operaciones 2; 12
    perfectas). PDF de 2 páginas con el mismo flujo (pág. 1 energía,
    pág. 2 resto + ambiental + detalle).
  - Semana 07–13 sep (con huecos): celdas rojas, 2 salas afectadas (Sala
    de Operaciones 2 y Zona Azul), nota final oculta (7 días-hueco de
    sala).
- Contexto git: origin/main ya tenía una versión de tabla (9898bc4,
  15-sep, "solo huecos"); esta es la iteración con fila agregada por sala,
  listas de perfectos y detalle, según las capturas. El local sigue
  divergido (5 commits detrás de origin/main) y este trabajo queda sin
  commitear.
- Nota: el PDF del endpoint sale sin cabecera/pie de Chrome
  (`--no-pdf-header-footer`); las capturas del trabajo eran impresiones
  del navegador (fecha, título, URL localhost y "1/2" son de Chrome).

## Sesión 05-oct-2026 — Sync full de AMBAS bases (energía + ambiental)

### Pedido
- Generar dump + restore de las dos bases (energía y ambiental) para traer
  los últimos datos a local.

### Ejecutado
- Postgres locales caídos al inicio: `pg_ctl -D C:/pgsql16/data` (energía,
  5432) y `-D C:/pgsql16/data-valhalla` (ambiental, 5433) → ambos arriba.
- Túneles manuales con keepalives ANTES del script (lección 07-sep) en 55432
  y 5435 → `sync_db.sh {energia,ambiental} full` los reusó.
- Energía: dump `backups/energia/energia_prod_20261005_115418.dump` (1.6 GB,
  -Fd -j 4 -Z 5) → restore en 127.0.0.1:5432. Verify: prod=11,325,953 vs
  local=11,325,104 (+849 filas en vivo, normal); última local 11:54:36 Lima
  (= inicio del dump) vs prod 12:22:18 Lima.
- Ambiental: dump `backups/ambiental/ambiental_prod_20261005_122231.dump`
  (236 MB) → restore en 127.0.0.1:5433. Verify: prod=16,187,493 vs
  local=16,187,455 (+38 filas en vivo, normal); última local 12:21:45 -05
  (= inicio del dump) vs prod 12:26:41 -05.
- Túneles cerrados a mano (PIDs reales 28848/3680 vía Get-CimInstance;
  puertos 55432/5435 confirmados cerrados). Dumps viejos del 07-sep
  eliminados (quedan 14-sep, 28-sep, 05-oct por base).

## Sesión 05-oct-2026 — Análisis de huecos semana 28-sep → 04-oct

### Pedido
- Análisis de huecos de la última semana completa (28-sep al 04-oct), con la
  data recién sincronizada.

### Ejecutado
- Exports regenerados con data fresca:
  - `analisis_huecos.py --export` → `analisis/cobertura_diaria.csv` (8,289
    filas, 86 puntos; meta: 11,325,104 lecturas, 37 puntos con datos, 177
    días-hueco y 27 episodios históricos, global_max_day 2026-10-05).
  - `analisis_huecos_ambiental.py --export` → 72,890 días-combo salas
    (+266 vs 28-sep) y 12 días-combo puntos (track de prueba 2024).
- Webapp `webapp.py` levantada en :8000; `GET /api/reporte-semanal
  ?desde=2026-09-28&hasta=2026-10-04`:
  - **Energía 96.2%** (32 puntos, 28 al 100%, **0 días-hueco**; 4 con días
    parciales: Sanna Llave general cuarto de bombas 03–04-oct (81%/69%),
    Oechsle Chiller 1 04-oct (67%), Chiller 2 29-sep (88%), TF-UMAS 2
    02-oct (70%)).
  - **Ambiental 100.0%** (13/13 salas y 38/38 combos OK, sin incidencias).
- PDF generado: `analisis/reporte_cobertura_2026-09-28_a_2026-10-04.pdf`
  (138 KB). Página:
  http://localhost:8000/reporte-semanal?desde=2026-09-28&hasta=2026-10-04

## Sesión 05-oct-2026 — Reporte de excedencias semana 21–27 sep

### Pedido
- Mismo análisis de datos que sobresalen de lo normal (excedencias vs
  umbrales) para la semana 21–27 sep, tras el de 28-sep → 04-oct.

### Ejecutado
- Cobertura 21–27 (exports del 05-oct): energía 96.2% (32 puntos, 28 al
  100%, 0 días-hueco; incidencias: TF-UMAS 2 ×2, TG-TR2 ×2, Chiller 1 ×1,
  Sanna Llave general ×1) y ambiental 100% (13/13 salas). PDF:
  `analisis/reporte_cobertura_2026-09-21_a_2026-09-27.pdf` (138 KB).
- Excedencias energía (928 alertas): 863 voltaje moderado + 5 crítico,
  45 corriente moderado + 12 crítico, 3 energía moderado.
  - Chiller 1 (Oechsle): reactiva inductiva 92.3% / 89.6% / 87.9% del
    activo los días 21, 23 y 25-sep (límite 30%). Sin excesos de consumo
    ni demanda esa semana.
  - Corriente crítica "equipo apagado" el 21-sep: RX-PB (00:44),
    Aire acondicionado cocina (08:15–10:48, 8 eventos), Sala de
    operaciones Piso 4 (22:30–23:48, 3 eventos).
  - Tensión: moderadas 21–24 sep (212–216 V en 12 puntos Sanna);
    "críticas" del Data center UPS (~10,460–10,530 V con nominal 220 V)
    = error de unidad/medición, igual que la semana siguiente.
- Excedencias ambiental: CO2 hasta 2,156 ppm (Sala de Operaciones 2;
  también SO5 1,879, SO1 1,676, SO3 1,507, SO4 1,452, Zona Azul 1,241);
  TEMP Subestación 30.8 / Sala UPS 30.2 / Zona Roja 27.8 y Ducto
  resonador frío (prom. 14.5); HUM Operaciones 5 prom. 67.4 (máx 85).
- Reporte guardado en `analisis/reporte_excedencias_2026-09-21_a_2026-09-27.md`
  (con umbrales, hallazgos y comparación vs 28-sep → 04-oct).

---

## Sesión 07-oct-2026 (casa) — Monitor en vivo (tiempo real)

### Objetivo (pedido del usuario)
Los reportes actuales son batch (cada lunes + restore): ven el problema
después. Se quiere una app interna bien diseñada que muestre **en tiempo real
minuto a minuto** qué punto dejó de traer información, para todas las empresas
(Sanna y Oechsle hoy), con drill-down General → Empresa → Punto, escalable a
cientos de tiendas. Decisiones acordadas: fuente = **producción vía túnel SSH**;
empaquetado = **pestaña/página dentro de `webapp.py` (:8000)**; alcance =
**energía + ambiental**; notificaciones = **solo tablero visual** por ahora.

### Implementado
- **`src/monitor.py`** (nuevo): estado en vivo multi-base. No escanea la tabla
  completa: una sola consulta acotada a la ventana del día (o 24 h si la
  fuente está congelada) obtiene `max(created_at)` + conteo por punto/combo.
  Semáforo por antigüedad de la última lectura y por **ritmo** (degrada si
  reporta <70% de lo normal). Detección de **fuente congelada** (sin datos
  globales >30 min): mide contra el horizonte y avisa, para no pintar todo
  rojo cuando es una copia/restore.
- **Ambiental**: cadencia **real por combo** (lecturas/día de los últimos 14
  días, cacheada 30 min) en vez de un valor fijo; bucket del heatmap = cadencia
  del sensor. Energía usa 1 lectura/min.
- **`webapp.py`**: `GET /monitor` (página), `GET /api/monitor/estado?base=`,
  `GET /api/monitor/lecturas?base=&ids=&desde=&hasta=` (minuto a minuto,
  ≤40 ids, ≤10 días). Importa `src.monitor`.
- **`web/static/monitor.html`** (nuevo): tema oscuro igual a `/gaps`; KPIs,
  banner de fuente, breadcrumb, cards por empresa/sede, tabla por tablero/sala
  con semáforo + barra de disponibilidad, detalle minuto a minuto (reusa el
  heatmap ECharts que le gusta al usuario), bandeja de incidentes abiertos,
  auto-refresh 30 s y enlaces directos `?base=&empresa=&sede=&uid=`.
- **`web/static/index.html`**: links "Monitor" y "Huecos" en el header.
- `docs/dashboard_huecos_spec.md` no se tocó (documenta el pipeline batch).

### Hallazgos / validación
- **La copia local de energía está congelada al 24-sep-2026** (13 días): el
  monitor lo detecta y lo explica. **Resuelto**: se apuntó energía a
  producción por túnel (`ENERGIA_DB_PORT=55432`, `USE_SSH_TUNNEL_ENERGIA=true`)
  y ambiental a 5435 (`USE_SSH_TUNNEL_AMBIENTAL=true`); ahora `python webapp.py`
  levanta los túneles solo y ambas bases quedan en vivo (verificado: max_global
  = ahora; Sanna 97.8%, Oechsle 96.5%, Scotiabank 100%). El Postgres local
  sigue en 5432, por eso el túnel usa 55432.
- **Ambiental está vivo** (valhallad 5435 en casa) y el monitor **detectó una
  degradación real en curso**: Zona Verde (CO2/HUM/TEMP) reportaba 288/día y
  cayó a 46–52 el 6–7-oct (→ 33%, "Bajo ritmo"); Zona Roja al 67%. Sanna
  global 92.4%.
- Energía: Oechsle 94.9% y Sanna 95.4% al horizonte; BK/KFC/Madam Tusan/Pizza
  Hut caídos desde hace meses; 48 puntos de inventario inactivos (no cuentan
  en la disponibilidad).
- Performance: `estado` energía ≈0.09 s; ambiental ≈1–3 s (primera consulta
  en frío, luego cache 20 s). Recomendación DBA: índice
  `(indicator_device_id, created_at DESC)` en valhalladb para que el "última
  lectura por combo" sea instantáneo (el actual tiene `room_id` en medio).
- QA: `py_compile` + `node --check` del JS + Chrome headless (`--dump-dom` y
  `--screenshot`) sobre las 4 vistas (general/empresa/sede/detalle) y ambas
  bases. Sin errores de consola.

### Rediseño según feedback del usuario (misma sesión)
El monitor debía ser una "pantalla de TV" que se entiende de un vistazo:
- **Solo HOY** (antes mostraba 2 días en el detalle): el tablero y el detalle
  usan el día en curso y excluyen el minuto en curso (siempre salía rojo).
- **Tienda + punto, sin nivel de sede**: `tienda` = empresa + sede
  (campo nuevo en `src/monitor.py`); el árbol pasó de empresa→sede→punto a
  tienda→punto. En ambiental la etiqueta incluye la sala para desambiguar
  (CO2/TEMP/HUM se repiten por sala).
- **Sin clics**: al abrir `/monitor` carga solo el **tablero en vivo** (todas
  las filas = puntos activos, columnas = minutos de hoy, verde/rojo), con
  auto-refresh cada 20 s. Vista "Resumen" (tiendas → puntos) es secundaria.
- **Ruido**: se ignoran huecos aislados de 1-2 min (smear por cadencia +
  denoise de 3 min); solo se ven cortes reales. Caídos se ordenan arriba
  (`yAxis.inverse`).
- Verificado con Chrome headless vía CDP (Node + WebSocket) porque
  `--virtual-time-budget` se cuelga con el heatmap grande; el canvas de
  ECharts (`getDataURL`) y las capturas confirman el render correcto.
- **Feedback: "no sé qué sala/punto me estás mostrando".** Causa: en ambiental
  las etiquetas repetían `CO2/HUMIDITY/TEMPERATURE` sin la sala, y la empresa
  de prueba "Machu picchu food" tiene una sala llamada literalmente
  **"Punto de Monitoreo"**. Fix: (1) esa empresa se excluye (`EXCLUIR`);
  (2) etiquetas: si hay una sola tienda → `Sala · Indicador` (ambiental) /
  `Punto` (energía); si hay varias → `Tienda · [Sala ·] Punto`;
  (3) `/monitor` se sirve con `Cache-Control: no-store` para que no quede
  HTML viejo en caché. Ambiental queda solo con Sanna San Borja (38 combos).

### Pendiente / ideas (fase 2)
- Notificaciones automáticas (email/webhook/WhatsApp) al cruzar umbral.
- Página ambient: `analisis_huecos_ambiental.py` tiene el export corrido
  (deuda ya registrada); el monitor NO usa esos JSON, va en vivo.
- Si se apunta a producción, revisar `statement_timeout` y pooling para
  cientos de tiendas (hoy LATERAL/ventana por índice; cache 20 s).

## Sesión 09-oct-2026 — Sync full de AMBAS bases (energía + ambiental)

### Pedido
- Generar dump + restore de las dos bases (energía y ambiental) para traer
  los últimos datos a local.

### Ejecutado
- Postgres locales caídos al inicio: `pg_ctl -D C:/pgsql16/data` (energía,
  5432) y `-D C:/pgsql16/data-valhalla` (ambiental, 5433) → ambos arriba.
- Túneles manuales con keepalives ANTES del script (lección 07-sep) en 55432
  y 5435 → `sync_db.sh {energia,ambiental} full` los reusó.
- Energía: dump `backups/energia/energia_prod_20261009_101803.dump` (1.6 GB,
  -Fd -j 4 -Z 5) → restore en 127.0.0.1:5432. El túnel 55432 murió
  ("Connection reset by peer") justo tras el dump → el verify automático dio
  prod=?; se reabrió el túnel y verificó a mano: prod=11,490,715 vs
  local=11,489,507 (+1,208 filas en vivo, normal); última local 10:18:30 -05
  (= inicio del dump) vs prod 10:57:39 -05.
- Ambiental: dump `backups/ambiental/ambiental_prod_20261009_105806.dump`
  (237 MB) → restore en 127.0.0.1:5433. Verify OK: prod=16,225,030 vs
  local=16,224,995 (+35 filas en vivo, normal); última local 10:57:37 -05 vs
  prod 11:02:38 -05.
- Dumps viejos del 14-sep eliminados (quedan 28-sep, 05-oct, 09-oct por base).
- Túneles cerrados a mano (PIDs reales 20940/20716 vía Get-CimInstance;
  puertos 55432/5435 confirmados cerrados). Postgres locales quedan arriba.
- LECCIÓN: el keepalive no evita del todo el reset del túnel tras la
  transferencia grande (murió al terminar el dump de energía); el dump quedó
  íntegro (sha256 OK) y solo falló el verify de prod → reabrir túnel y
  verificar a mano (no repetir dump/restore).

## Sesión 09-oct-2026 — Respuesta al jefe + formato nuevo de reporte semanal

### Pedido
- El jefe revisó los reportes 21–27 sep y 28-sep–04-oct y pidió 3 cosas:
  (1) por cada punto sin lectura (20 Oechsle + 11 Sanna): ¿sensor instalado
  o solo creado? ¿desde cuándo no envía?; (2) sacar del reporte Pizza Hut,
  Burger King, KFC, Madam Tusan y TestCorp; (3) ¿los 11 de Sanna están en
  el alcance contratado o nunca se consideraron?
- Luz verde para evidencia + formato nuevo (Scotiabank se queda en el
  reporte; evidencia solo con plataforma; proponer formato propio).

### Evidencia (solo lectura, DB local sync 09-oct)
- Exports regenerados: `analisis_huecos.py --export` (8,417 filas, 86
  puntos, 11,489,507 lecturas) + `analisis_huecos_ambiental.py --export`.
- Query inventario + agregados de `readings_reading` sobre 40 ids
  (script temporal, no quedó en el repo).
- **Hallazgo 1**: son **22 Oechsle + 13 Sanna** (no 20/11): el reporte
  colapsaba nombres duplicados con `set()` ("Reserva" ×2 Oechsle,
  "Espacio"/"Otros" ×2 Sanna). Bug corregido (clave por `point_id`).
- **Hallazgo 2**: los 35 tienen `is_active=false`, device
  "Device falso/false *" inactivo y **0 lecturas** (creados 29-dic-2025
  Sanna, 09/18-feb-2026 Oechsle) → solo creados, sin sensor. No requieren
  campo; falta confirmar baja vs reserva con contrato (pregunta 3: la DB
  no la responde).
- **Hallazgo 3**: caídos con sensor real y fecha exacta — BK 108
  (→31-jul-2026, 60k lecturas), KFC 109 (→11-abr-2026), MT 166/167
  (→14-may-2026), PH 107 (→20-abr-2026; su device se llama "falso" pero
  transmitió 63,948 lecturas → el nombre solo no prueba nada).
- `installation_date` en NULL en los 40: columna sin uso, no sirve de
  evidencia. Evidencia guardada en `docs/respuesta_jefe_puntos_sin_lectura.md`.

### Formato nuevo
- `webapp.py`: `EMPRESAS_FUERA_DE_ALCANCE` (las 5 pedidas) filtrada en
  `_reporte_energia` (/gaps intacto); `puntos_sin_datos` ahora lista de
  dicts por `point_id` con `estado` (sin_sensor/sensor_mudo/caido) +
  `detalle`; + `alcance` (sitios en scope) y `dias_parciales` en el API.
- `scripts/analisis_huecos.py --export`: suma `dispositivo` y
  `punto_activo` por punto en `cobertura_resumen.json` (fuente del estado).
- `web/static/reporte_semanal.html` reescrito: título con rango + fecha de
  generación, línea de alcance, KPIs (cobertura, X/Y sin incidencias,
  parciales, hueco), tabla solo con incidencia, perfectos en una línea +
  `<details>` (oculto en print), anexo "Puntos pendientes estructurales"
  agrupado por sede+estado con conteos exactos, misma compactación en
  ambiental.
- Verificado API + screenshots Playwright + texto de PDFs (1 pág.,
  0 menciones de empresas muertas) para 21–27 y 28–04. PDFs nuevos
  reemplazan a los del 05-oct en `analisis/reporte_cobertura_*.pdf`.
- Webapp corriendo en :8000 (log `logs_webapp_reporte.log`).

## Sesión 09-oct-2026 — Reporte Sanna por gateway (estilo sketch)

### Pedido
- Reporte como el sketch dibujado a mano: jerarquía EMPRESA → GATEWAY →
  sala por día, solo Sanna, mostrando TODAS las salas, con promedios en
  cada nivel (semanal de gateway, de día ×7 → promedio de la semana,
  promedio marca/empresa). Lista de despliegue: 26 EUIs en 3 gateways.

### Hallazgo previo (verificación contra DB)
- De los 26 EUIs, **13 son sensores ambientales** (`equipments_device`,
  valhalladb) = exactamente las 13 salas del reporte actual (G1: 7 salas,
  G2: Subestación, G3: SO1–SO5). Los **otros 13 son medidores de ENERGÍA**
  (ADW300/ADW210 en la base `energy`, verificados por `dev_eui`) y NO van
  en este reporte de salas (ya están en el reporte de energía).
- Resolución sala→gateway en vivo por `dev_eui` (robusto ante tildes/caso).

### Implementado
- `webapp.py`: `GATEWAYS_SANNA` (EUI→gateway) + `GET /api/reporte-gateways`
  (días, salas con % = promedio de sensores topado en 100 — el pct
  ritmo-relativo llega a 128% en Zona Roja y rompería la lectura de
  "cobertura"), + `GET /reporte-gateways` + `GET /reporte-gateways/pdf`.
- `web/static/reporte_gateways.html`: tabla estilo sketch (EMPRESA con
  caja azul, gateways con cajas rojas, filas PROMEDIO por gateway/día,
  pie naranja con promedios de día ×7 → promedio semana). Enlace mutuo
  con `/reporte-semanal`.
- Verificado: API (13 salas, G1:7/G2:1/G3:5, prom empresa 99.9% semana
  28-sep–04-oct), screenshot Playwright y PDF
  `analisis/reporte_gateways_sanna_2026-09-28_a_2026-10-04.pdf` (1 pág.;
  los nombres largos se cortan en el extractor de texto pero están
  completos en el render).

### Extensión (misma sesión): gateways de ENERGÍA Sanna
- Pedido: mismo sketch pero solo energía de Sanna.
- `webapp.py`: `_reporte_gateways_energia` (lee `cobertura_diaria.csv`,
  filtra Sanna, agrupa puntos por gateway vía `dev_eui` en vivo; % crudo
  lecturas/1440 como el semanal) + `GET /api/reporte-gateways-energia` +
  `GET /reporte-gateways-energia/pdf`. G1 = TG-P1/TG-RT/TGA (12 equipos),
  G2 = TFC-BAF (cuarto de bombas); G3 no tiene medidores de energía.
- `reporte_gateways.html` parametrizada con `?alcance=energia` (misma
  plantilla, títulos/API/PDF según alcance); enlaces mutuos en el toolbar
  del reporte semanal. Mini-fix CSS: `white-space:nowrap` en celdas
  PROMEDIO.
- Verificado semana 28-sep–04-oct: 13 equipos, prom empresa 96.2%
  (consistente con el semanal), screenshot Playwright y PDF
  `analisis/reporte_gateways_sanna_energia_2026-09-28_a_2026-10-04.pdf`.

## Sesión 09-oct-2026 — Fix alineación tabla de gateways

### Pedido
- La tabla del reporte por gateway mostraba los encabezados corridos una
  columna y la última columna (Dom 04) sin título; dejarla toda alineada.

### Causa y fix
- `web/static/reporte_gateways.html`: el encabezado emitía solo 2 `<th>` vacíos
  (EMPRESA/GATEWAY) mientras las filas de datos tienen 3 celdas antes de los
  días (EMPRESA/GATEWAY/SALA) → todos los encabezados de día corrían 1 columna
  a la izquierda y el Dom 04 quedaba sin encabezado; además la fila PROMEDIO
  por gateway estaba desalineada 1 columna respecto a las filas de equipos.
  Fix: 3er `<th>` vacío + `<td>` vacío inicial en la fila PROMEDIO.
- PDFs regenerados con la plantilla corregida (rango 28-sep–04-oct):
  `analisis/reporte_gateways_sanna_energia_*.pdf` y
  `analisis/reporte_gateways_sanna_*.pdf`. Verificado con screenshots
  Playwright: encabezados sobre la columna correcta y cuarto de bombas con
  Vie 02=94.2% / Sáb 03=81.4% / Dom 04=69.4% bajo sus días.

---

## Sesión 11-oct-2026 (casa) — Reporte semanal de conectividad en PDF (28-sep a 04-oct)

### Pedido
- Rehacer, más ordenado, el PDF `~/Downloads/reporte_gateways_sanna_energia_2026-09-28_a_2026-10-04.pdf`
  (solo energía, agrupado por gateway) para el jefe: energía + ambiental, **sin
  gateways** (la asignación punto→gateway no está en la DB) y con los días
  por debajo del 90% en otro color.

### Ejecutado
- `scripts/reporte_conectividad.py` (nuevo): consulta EN VIVO ambas bases vía
  `src.db` (read-only + `validate_sql`), arma un HTML auto-contenido y lo
  imprime a PDF con Chrome/Edge headless (1 hoja carta). Default = semana
  pasada lun–dom, empresa Sanna, salida
  `~/Downloads/reporte_conectividad_<empresa>_<desde>_a_<hasta>.pdf`;
  opciones `--desde/--hasta/--empresa/--umbral/--out/--html/--chrome`.
- Métrica = lecturas del día (hora Lima) / esperadas, tope 100%: energía
  1,440 por equipo (filas con EPpos, criterio de `analisis_huecos.py`),
  ambiental 288 por sensor. La sala toma su sensor más bajo del día (una
  falla no se diluye en un promedio); subfilas por sensor solo si en un día
  en rojo los sensores difieren. Rojo = valor impreso (1 decimal) < 90.
- Energía agrupada por tablero (sí está en la DB), llave/is_main primero;
  columna "Semana", fila "Promedio del día" y detalle de cada día en rojo
  con lecturas recibidas y tiempo total sin datos.
- Excluye el inventario sin ningún dato en 5 semanas (Sanna: 13 circuitos
  "Device Falso") y lo aclara en el pie. Aborta si la base no cubre el rango
  (copia local vieja → rojos falsos) o si el rango incluye el día en curso.
- `tests/test_reporte_conectividad.py`: 12 pruebas unittest sin DB
  (`python -m unittest discover -s tests`). ~10 s por reporte.

### Resultado (semana 28-sep → 04-oct)
- ⚡ 96.2% (13 equipos, 4 tableros). **91/91 celdas idénticas al PDF de
  referencia** y mismos promedios por día (96.9 … 94.5) y semana. Único
  rojo: **Llave general cuarto de bombas** (punto 3): sáb 03 81.4% (1,172 de
  1,440) y dom 04 69.4% (1,000 de 1,440), patrón intermitente (muchos huecos
  cortos; el más largo 41 min, dom 04 08:59–09:40). Siguió el lun 05 (86.9%)
  y se recuperó desde el mar 06 (97.4–99.6%).
- 🌡 99.5% (13 salas, 38 sensores), todas ≥94.8% (la más baja: Ducto
  resonador magnético, sensor TS-201-TH con ~273–281 lecturas/día).
  91/91 celdas verificadas contra una consulta independiente por `room_id`.

### Lecciones
- Chrome headless en macOS escribe el PDF de `--print-to-pdf` pero el
  proceso no termina (`subprocess.run` colgado hasta el timeout): esperar a
  que el archivo termine en `%%EOF` y matar el grupo de procesos
  (`start_new_session` + `killpg`; `taskkill /T` en Windows).
- Decidir "equipo activo" contando 5 semanas tardaba ~16 s en energía;
  `EXISTS` por índice solo para los ids sin datos en la semana → ~10 s total.
- Los túneles que abre `ensure_tunnel` desde scripts sueltos sobreviven al
  proceso: el script llama a `tunnel.close_tunnel()` al final (solo cierra
  los que abrió él; reutiliza y deja vivos los ajenos).

### 2º pedido (misma sesión): menos texto + todas las empresas + conectividad final
- Quitados del PDF: "Generado el…", la explicación "Cada celda es…/En rojo…",
  el detalle "Días por debajo del 90%", el "✓ Todas las salas…" y el pie de
  circuitos excluidos (esa info queda solo en la salida de consola).
- Una página por empresa con datos (semana o 4 semanas previas), orden
  alfabético; empresas de prueba fuera con `src.monitor.EXCLUIR`.
  `--empresa` ahora es repetible (default: todas). Archivo:
  `reporte_conectividad_<desde>_a_<hasta>.pdf` (con `_<empresa>` si se filtra).
- Al final de cada empresa: tabla "Conectividad promedio de la semana" con
  Energía | Ambiental | Conectividad final. Final = **promedio simple** de los
  módulos con las cifras impresas y redondeo .5 hacia arriba (el usuario dijo
  "sumando ambos": 96.2 + 99.5 = 195.7 no es un %, se divide entre 2). Con
  un solo módulo, la final es ese módulo ("solo energía").
- Inventario de empresas (hoy): energía tiene 8; con datos solo Oechsle (18
  de 40 puntos), Sanna (13 de 26) y Scotiabank (1). Sin datos: BK (31-jul),
  Madam Tusan (14-may), Pizza Hut (20-abr), KFC (11-abr), TestCorp (nunca).
  Ambiental tiene 13 empresas; con datos solo Sanna (38 sensores); el resto
  dejó de enviar entre 2023 y abr-2026 (Zeia y Machu picchu food = prueba).
  BanBif no existe en ninguna de las dos bases.
- Semana 28-sep → 04-oct: **Oechsle 96.0%** (solo energía; rojos: Chiller 1
  dom 04 67.4%, TF-UMAS 2 vie 02 69.8%, Chiller 2 mar 29 88.0%), **Sanna
  97.9%** = (96.2 + 99.5) ÷ 2, **Scotiabank 100.0%** (solo energía).
- Verificado contra consultas independientes: 224 celdas de energía (3
  empresas) + 91 ambientales, promedios desde conteos crudos y las 3
  conectividades finales; Sanna energía sigue 91/91 igual al PDF original.
  Ojo: promediar a mano las celdas YA redondeadas puede diferir 0.1 del
  "Semana" impreso (p. ej. TD-A1: 97.14 vs real 97.16 → 97.2); el reporte
  promedia los valores sin redondear.
- Scotiabank el lun 28 tuvo 1,441 filas (1 minuto duplicado): el tope de 100%
  por celda lo absorbe.
- Consulta ambiental de "última lectura por combo" con subconsulta por id dio
  timeout (el índice es `(indicator_device_id, room_id, created_at)`); un solo
  `GROUP BY` sobre toda la tabla tarda ~9 s.

### Push (misma sesión): integración con el trabajo del 09-oct
- Al pushear, `origin/main` tenía 3 commits del trabajo (PC Windows, 09-oct:
  reportes por gateway, `/reporte-semanal/pdf`, respuesta al jefe) y la casa
  tenía sin commitear lo del 24-sep, 07-oct y 11-oct. Se hizo fast-forward a
  `origin/main` y se reaplicó lo de casa encima (historia lineal, sin merge):
  - `webapp.py` = versión del trabajo + el monitor en vivo (import + 3
    endpoints).
  - Reporte semanal web (`reporte_semanal.html` + `_reporte_energia/
    _reporte_ambiental`): se quedó la versión del trabajo (02/09-oct), que
    reemplaza a la variante de casa del 24-sep (mismo feedback del jefe,
    implementado dos veces). La variante del 24-sep quedó en la rama LOCAL
    `respaldo/casa-24sep-11oct` (no pusheada).
  - Bitácora: sesiones de ambos lados en orden de fecha, sin perder líneas.
- Lección: trabajar en dos PCs sin pushear diverge. Antes de empezar en
  cualquiera de las dos: `git pull`; al terminar: commit + push.
