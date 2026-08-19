# ZEIA-IA — Agente de IA para análisis de consumo energético

Agente conversacional que responde preguntas en español sobre los datos de
monitoreo energético almacenados en la base PostgreSQL `energy` (producción).

## Arquitectura

```
cli.py                  Chat en terminal
webapp.py               App web (FastAPI): chat + gráficos + voz → python webapp.py (:8000)
web/static/index.html   UI de chat (selector de modelo, chips, 🎙 modo
                        conversación por voz con VAD: detecta habla/silencio)
web/static/vendor/      echarts.min.js + marked.min.js locales (sin CDN)
src/
  config.py             Carga .env; DEFAULT_MODEL y credenciales
  tunnel.py             Túnel SSH (reutiliza uno existente si el puerto ya está abierto;
                        con USE_SSH_TUNNEL=false en .env NUNCA abre el túnel → DB local)
  db.py                 SQLAlchemy + seguridad: read_only, statement_timeout,
                        validación SELECT-only (sqlparse), LIMIT y truncado de resultados;
                        re-verifica el túnel SSH en cada consulta (reconexión automática)
  tools.py              Function calling: list_schemas/list_tables/describe_table/
                        run_query/render_chart (gráficos: specs capturadas en agent.py
                        y renderizadas por la web; en CLI se ignoran)
                        + list_documents/extract_pdf_text (leer PDFs de la raíz)
  pdf_tools.py          Lista y extrae PDFs del proyecto (pdf-inspector → Markdown
                        con tablas; fallback pypdf). Facturas Kallpa: enero-2025.pdf…
                        diciembre-2025.pdf (Tiendas Peruanas S.A. = Oechsle Salaverry)
  elevenlabs.py         Voz (ElevenLabs): transcribe() STT scribe_v1 (es),
                        synthesize() TTS eleven_flash_v2_5, clean_for_speech()
                        (quita markdown/emojis, máx 1,500 chars). Sin API key la
                        UI deshabilita el mic. Endpoints: /api/voice/{status,
                        transcribe,tts,speak} (audio crudo en body, máx 10 MB).
  speech.py             Resumen HABLADO de las respuestas (la voz no lee el
                        texto: 1-3 frases con cifras clave vía LLM rápido
                        VOICE_SUMMARY_MODEL; fallback = clean_for_speech).
  agent.py              Loop del agente (OpenRouter, máx 15 iteraciones, temperature 0.1)
  prompts.py            System prompt con TODO el conocimiento del dominio (¡mantener al día!)
docs/
  bitacora.md           ★ Historial de sesiones: hallazgos, decisiones, resultados
                        (leer al inicio de toda sesión; actualizar al final)
  question_catalog.md   Catálogo de preguntas de negocio + diccionario de
                        indicadores (lenguaje cliente → columnas DB)
  reportes_analisis.md  Análisis de los reportes mensuales actuales (patrones
                        adoptados: consumo base, P95, umbrales 3 niveles…)
scripts/
  test_connection.py    Verifica túnel + login + lista esquemas
  introspect.py         Regenera docs/schema.md y docs/schema_full.json (con filas de ejemplo)
eval/
  questions.yaml        Preguntas de prueba con hechos esperados
  questions_carga.yaml  Prueba de carga: 3 meses × 2 puntos/tableros
  compare_models.py     Corre preguntas × modelos (--file para elegir YAML);
                        guarda JSON en eval/results/
docs/
  schema.md             Esquema legible de la DB (63 tablas, esquema public)
```

## Infraestructura de datos

- **Túnel SSH**: `ssh -i energy.pem -N -L 55432:172.31.29.136:5432 ubuntu@54.242.41.196`
  (el código lo levanta solo si el puerto no está abierto). `energy.pem` y `.env`
  están gitignored — NUNCA commitear.
- **DB**: PostgreSQL 16, base `energy`, usuario `postgres` (el manual decía
  `Postgres`/`Energy`/puerto 5435 — todo eso estaba mal; lo correcto está en `.env`).
- **⚠️ DB local activa (18-ago-2026)**: backup completo de producción
  restaurado en PostgreSQL 16.15 local (Homebrew, `127.0.0.1:5432`, base
  `energy`, 9.2M lecturas). `.env` apunta local con `USE_SSH_TUNNEL=false`.
  Dump en `backups/energy_prod_20260818_123052.dump` (restaurar con el
  pg_restore 18 de libpq, no el de PG 16). Para volver a producción:
  `DB_PORT=55432` + `USE_SSH_TUNNEL=true` en `.env`.

## Conocimiento clave del dominio (también en src/prompts.py)

- Jerarquía: `enterprises_enterprise` → `enterprises_energyheadquarter` →
  `enterprises_electricalpanel` → `devices_device` → `enterprises_measurementpoint`.
- `readings_reading` (~8.3M filas, 1 lectura/min/punto): columnas case-sensitive
  que REQUIEREN comillas dobles: `"P_value"` (W), `"EPpos_value"` (contador
  acumulado kWh → consumo = max−min), `"PF_value"`, `"Ua_value"`, etc.
- `created_at` es timestamptz en UTC; para horas/días locales usar
  `AT TIME ZONE 'America/Lima'` (Perú, UTC-5). Horas punta: 18:00–23:00 lun–vie.
- **Trampa de timezone**: NUNCA comparar created_at (timestamptz) contra un
  `::date` pelado (se castea en UTC → corta el día a las 19:00 Lima). Bounds
  siempre `::timestamp AT TIME ZONE 'America/Lima'` o literales '-05'.
  "Últimos N días" = N días calendario completos terminando ayer (hoy es
  parcial, excluir o etiquetar). Ver bitácora sesión 3 para el bug completo.
- `alerts_alert`: alert_status (moderate/critical), subtipos (fluctuation/power/
  current/energy_subtype), `notes` con descripción legible.
- Tarifas: `enterprises_billingdata` (S//kWh punta/fuera punta, PEN),
  `enterprises_billingcycle`, `enterprises_power` (potencia contratada kW).
- **Sensible**: `historical_readinghistory.data` contiene credenciales de
  dispositivos; `accounts_user`/`authtoken_token` datos personales. No exponer.
- Empresas reales: Sanna, BanBif, Oechsle, Pizza Hut, Burger King, KFC,
  Madam Tusan. `TestCorp_Email` es de pruebas (ignorar).
- **Datos activos**: solo Sanna y Oechsle reportan en vivo. Madam Tusan llega
  hasta 14-may-2026; Pizza Hut 20-abr-2026; KFC/BK 11-abr-2026. Preguntas con
  fechas posteriores deben responder "sin datos" (ver regla en prompts.py).
- **Nombres de sedes**: son simples ('Salaverry', 'San Borja') y 3 sedes
  distintas se llaman 'Salaverry' (Oechsle/Pizza Hut/KFC... corregir: BK y KFC
  también). Filtrar por eh.id, no por texto concatenado ("Oechsle Salaverry"
  NO existe).
- **EPpos es contador acumulado**: JAMÁS SUM() directo; consumo = MAX−MIN por
  punto por periodo (para punta/fuera de punta, agrupar por punto Y DÍA antes
  de restar — ver patrón canónico en prompts.py).
- **Total de tablero/sede**: punto `is_main` si existe; si no, Σ puntos.
  NUNCA llave + subcircuitos (duplica). TG-RT no tiene is_main (su carga
  principal es el punto "Tomógrafo").
- **⚠️ Total de Oechsle Salaverry (criterio del cliente, sesión 8)**: el
  consumo de la sede = SOLO los 2 tableros "Llave general TG-TR1" (punto 75)
  + "TG-TR2 (TF-AA) - HVAC" (punto 76). NO sumar "Red normal" (67) ni
  "Llave General TGE-TR1" (69) aunque tengan is_main=true. Con esta regla el
  monitoreo queda ~33% por debajo de la factura → pendiente validar qué
  miden esos otros 2 puntos y el periodo de lectura del medidor BRG.
- **⚠️ Salto de contador TG-TR2 (punto 76) el 23-mar-2026**: +62,979 kWh en
  un día (único evento en feb-jul 2026). **EXPLICADO (sesión 9)**: el ADW300
  (dispositivo 71) contaba al 50% desde su instalación (12-feb): ~1,400
  kWh/día vs ~2,800 kWh/día que sumaban sus subcircuitos (49-50% exacto). El
  23-mar 16:11 lo corrigieron: el salto de +62,499 = la energía no contada
  acumulada (~45 días × ~1,390/día) y desde entonces marca 100% de sus
  subcircuitos. NO es defecto general del ADW300: el punto 75 (TG-TR1, mismo
  modelo) rinde 110-117% de sus subcircuitos de forma estable, y ningún otro
  punto de Oechsle/Sanna/Madam Tusan saltó. Marzo registrado (121 MWh) incluye
  la corrección → el real fue ~90 MWh. Los demás "duplicados" feb→mar de
  Oechsle son artefacto: las lecturas EPpos arrancaron progresivamente el
  07-14-feb (febrero parcial).
- **Tarifa Oechsle = 39.15 S//kWh** en billingdata: casi seguro error de
  digitación en la fuente (las demás son 0.65). Reportarlo con transparencia,
  no ajustar en silencio. Sanna NO tiene tarifa registrada (billing_data_id
  NULL); los reportes actuales usan ~0.155 S//kWh implícito → pendiente
  confirmar con el equipo.
- **Performance DB**: agregación 3 meses × 1 punto ≈ 25s; 2 puntos 60–100s.
  statement_timeout = 120s. Recomendación DBA: índice covering
  (measurement_point_id, created_at) INCLUDE ("EPpos_value","P_value").
- **⚠️ P_value: unidades INCONSISTENTES por dispositivo** (sesión 7): Sanna
  TGA en kW, Sanna "cuarto de bombas" en W, Oechsle en unidades raras
  (avg ~1.8, máx ~400 — nada cuadra con EPpos). NO usar P_value de Oechsle
  para demanda/potencia; validar unidades antes de reportar kW. El EPpos
  (contador) sí es confiable.
- **Facturas 2026**: enero-2026.pdf…junio-2026.pdf en la raíz (mismos campos
  que 2025). Precio 2026: 38.2–39.2 US$/MWh; mayo-2026 = 39.15 = billingdata
  exacto (tarifa validada). Demanda HFP superó 686 kW (ene/mar) sin recargo
  (Exceso HP/HFP = 0.000).

## Resultados de la comparación de modelos (eval 2026-07-28)

60 corridas (6 modelos × 10 preguntas), en `eval/results/run_20260728_170746.json`:

| Modelo | Calidad | Velocidad | Costo/10 preg. | Veredicto |
|---|---|---|---|---|
| qwen/qwen3-coder | alta, honesto ante datos faltantes | 18 s/preg. | $0.05 | **default** |
| google/gemini-2.5-flash | media-alta (errores numéricos puntuales) | 9 s/preg. | $0.04 | rápido/barato |
| openai/gpt-4.1-mini | alta | 15 s/preg. | $0.06 | alternativa |
| anthropic/claude-sonnet-4.5 | la mejor (transparencia + presentación) | 68 s/preg. | $1.78 | tier premium |
| deepseek/deepseek-v3.2 | buena | 182 s/preg. (hasta 8 min) | $0.19 | demasiado lento |
| meta-llama/llama-3.3-70b | alucina, rompe formato de tools | 11 s/preg. | $0.006 | descartado |

Prueba posterior (17-ago-2026, 4 preguntas representativas contra la DB local;
runs `20260817_151421`/`20260817_152246`): `deepseek/deepseek-v4-pro` y
`deepseek/deepseek-v4-pro-0813` lograron 4/4 correctas (103–111 s/preg.,
$0.27–0.33), honestos ante datos faltantes; **-0813** fue el más rápido y
barato. Candidato a selector web para analítica pesada (alternativa a
gpt-4.1-mini).

## Comandos

```bash
source venv/bin/activate
python scripts/test_connection.py     # verificar conectividad
python cli.py --verbose               # chat terminal (muestra herramientas/SQL)
python webapp.py                      # chat web con gráficos → http://localhost:8000
python scripts/introspect.py          # regenerar docs del esquema
python eval/compare_models.py --questions q1_empresas_puntos  # eval rápida
```

## Convenciones

- Python 3.9 (system) — usar `from __future__ import annotations` para `X | Y`.
- Respuestas del agente y comentarios del código de dominio en español.
- Solo lectura contra la DB: toda consulta pasa por `db.run_query` (validación
  + sesión read-only). No agregar rutas de escritura.
