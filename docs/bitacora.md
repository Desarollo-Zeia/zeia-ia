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

## Sesión 13 — 2026-08-20: Asistente multi-base (energía + ambiental) para clientes

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
