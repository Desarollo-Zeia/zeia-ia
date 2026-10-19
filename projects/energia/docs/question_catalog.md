# Catálogo de preguntas de negocio — ZEIA

Catálogo orientado al valor para el cliente (empresas con monitoreo energético:
clínicas, retail, restaurantes). Cada categoría incluye las preguntas que un
cliente haría y los **insights proactivos** que el agente debe ofrecer aunque
no se los pidan.

---

## 0. 🔌 Qué puede consultar el cliente (diccionario de indicadores)

El cliente puede preguntar en lenguaje natural; así se traduce a la DB
(`readings_reading`, 1 lectura/minuto por circuito):

| Lo que pregunta | Indicador | Columna / cálculo | Unidad |
|---|---|---|---|
| "consumo", "energía" | Energía activa | `"EPpos_value"` MAX−MIN en el periodo | kWh |
| "demanda", "potencia" | Potencia activa instantánea | `"P_value" / 1000` | kW |
| "corriente", "amperaje" | Corriente por fase | `"Ia_value"`, `"Ib_value"`, `"Ic_value"` (y `"In_value"`) | A |
| "voltaje", "tensión" | Voltaje fase-neutro / fase-fase | `"Ua_value"`, `"Ub_value"`, `"Uc_value"` / `"Uab_value"`, `"Ubc_value"`, `"Uac_value"` | V |
| "factor de potencia" | FP | `"PF_value"` (0–1; penalizable típico < 0.9) | — |
| "reactiva" | Potencia/energía reactiva | `"Q_value"` / `"EQpos_value"` MAX−MIN | var / kvarh |
| "aparente" | Potencia aparente | `"S_value"` | VA |
| "frecuencia" | Frecuencia de red | `"F_value"` | Hz |
| "armónicos", "THD", "distorsión" | THD voltaje/corriente | `"THDUa/b/c_value"`, `"THDIa/b/c_value"` | % |
| "desbalance" | Desbalance de corriente | (max−min fases)/promedio × 100 | % |
| "alertas", "eventos" | Alertas del sistema | tabla `alerts_alert` (+ `alerts_alertthreshold`) | — |
| "costo", "facturación", "soles" | Tarifas | `enterprises_billingdata` (+ `billingcycle`) | S/ |
| "potencia contratada" | Contrato | `enterprises_power.power_contracted` | kW |

Niveles de análisis disponibles: circuito → tablero → sede → empresa;
cualquier rango de fechas con datos (ojo con sedes sin datos recientes);
comparativas entre puntos/tableros/sedes y entre periodos.

---

## 1. 💰 Facturación y costos
Lo que más le importa al cliente: cuánto paga y cómo pagar menos.

**Preguntas del cliente:**
- ¿Cuánto consumí este mes y cuánto me costó (con mi tarifa)?
- ¿Cómo va mi consumo acumulado vs el ciclo de facturación anterior?
- ¿Qué porcentaje de mi consumo ocurre en horas punta (18:00–23:00, más cara)?
- ¿Cuánto habría pagado si hubiera movido ciertas cargas fuera de punta?
- ¿Mi demanda máxima se acercó a mi potencia contratada? ¿Estoy pagando de más
  por una potencia que no uso?

**Insights proactivos:**
- % de consumo en hora punta + costo estimado de moverlo.
- Potencia contratada sobredimensionada (demanda máx real « contratada → se
  puede bajar y ahorrar en el cargo fijo de potencia).

## 2. 🚨 Anomalías y calidad de energía
Proteger equipos y operación (crítico en clínicas como Sanna).

**Preguntas del cliente:**
- ¿Qué alertas hubo esta semana / hoy? ¿Cuáles son críticas?
- ¿Hubo sobrevoltajes o caídas de voltaje? ¿En qué circuitos?
- ¿Algún equipo se apagó inesperadamente?
- ¿Cómo está la distorsión armónica (THD) de mi instalación?

**Insights proactivos:**
- Picos de alertas repetitivas en un mismo circuito (equipo problemático).
- Sobrevoltaje crónico (>2% sobre nominal) que acorta la vida útil de equipos.
- Desbalance de fases detectado.

## 3. 📊 Patrones de consumo
Entender cómo opera el negocio.

**Preguntas del cliente:**
- ¿Cómo es mi curva de carga diaria / semanal?
- ¿Qué circuitos consumen más? (ranking)
- ¿Cuánto consume el aire acondicionado vs iluminación vs cocina?
- ¿Hay consumo de madrugada cuando el local está cerrado?

**Insights proactivos:**
- **Carga base nocturna alta**: consumo a las 3 AM que nunca baja → equipos
  que quedan encendidos (fugas de consumo). Cuantificar en kWh y S//mes.
- Fines de semana con consumo de día laborable (o al revés, según el rubro).

## 4. ⚡ Eficiencia y oportunidades de ahorro
El "qué hacer" accionable.

**Preguntas del cliente:**
- ¿Dónde están mis oportunidades de ahorro concretas? Cuantifícalas.
- ¿Mi factor de potencia me expone a penalización? (FP < 0.9 o reactiva > 30%)
- ¿Qué equipos conviene apagar/reprogramar y cuánto ahorraría?

**Insights proactivos:**
- FP bajo sostenido → riesgo de cargo por energía reactiva; cuantificar.
- Ranking de circuitos con mayor ahorro potencial estimado.

## 5. 🏢 Comparativas (multi-sede / multi-empresa)
Para grupos con varias sedes.

**Preguntas del cliente:**
- ¿Qué sede consume más por m² / por horario de atención?
- Compara el consumo de mis sedes este mes.
- ¿Qué sede tiene más alertas?

**Insights proactivos:**
- Sede que se desvía del patrón de las demás (outlier).

## 6. 🔧 Salud del monitoreo
Meta-información sobre el propio sistema (transparencia con el cliente).

**Preguntas del cliente:**
- ¿Mis equipos están reportando normalmente?
- ¿Desde cuándo tengo datos? ¿Hay huecos?

**Insights proactivos (MUY IMPORTANTE):**
- Si la pregunta cae en un periodo sin datos: decirlo explícitamente y
  ofrecer el rango disponible (BK/KFC hasta 11-abr-2026, Pizza Hut 20-abr,
  Madam Tusan 14-may; Sanna y Oechsle en vivo).

---

## Reglas de presentación (para el agente)

1. **Números primero**: toda afirmación con cifra, unidad y periodo.
2. **Soles cuando se pueda**: convertir kWh a S/ con la tarifa de la sede.
3. **Gráfico cuando ayude**: series temporales → línea; rankings → barras;
  composiciones (punta vs fuera punta) → pie/dona. Máx 2-3 por respuesta.
4. **Insight proactivo**: cerrar con una sección breve "💡 Para tener en
  cuenta" SOLO si hay un hallazgo respaldado por los datos consultados
  (nunca inventado, nunca repetir lo ya dicho).
5. **Accionable**: cada hallazgo con una recomendación concreta.
