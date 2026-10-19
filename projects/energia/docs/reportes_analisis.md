# Análisis de reportes actuales al cliente (12 láminas)

Fuente: reporte mensual real — Sanna San Borja, junio 2026.
Propósito: que el agente aprenda a producir análisis del mismo nivel y detectar
mejoras/oportunidades de producto.

---

## Estructura que usa hoy el reporte (patrón aprendido)

1. **Contexto**: sede, periodo, horario de operación, árbol de tableros
   (qué se monitorea y qué cuelga de qué).
2. **Resumen ejecutivo (3 KPIs)**: energía total (MWh + S/), consumo base
   nocturno (kWh/día), tablero de mayor consumo (MWh + S//mes).
3. **Consumo base vs día completo**: % del consumo que ocurre en horas de baja
   actividad. Definición que usan: **00:00–08:00 y 22:00–24:00**.
4. **Variación de consumo diario**: serie diaria + día alto (10-jun 5,257 kWh),
   día bajo (28-jun 3,657), promedio (4,811), delta (1,599 kWh). Narrativa:
   "el día bajo es la referencia de lo replicable".
5. **Drill-down de la variación por bloque**: qué tablero explica la
   diferencia día alto vs bajo → encontraron que es "otros no desagregados".
6. **Distribución por tablero**: barra apilada, MWh por tablero + "otros no
   desagregados" (llave general − suma de hijos).
7. **Comportamiento habitual → umbrales de alerta**: histograma de % tiempo
   por rango de potencia (89.8% entre 75–130 kW; mediana 98; P95 128) →
   proponen 3 niveles: caída <75, alta >130, crítica >150 kW.
8. **Revisión eléctrica**: desbalance de corriente por hora (promedio + P95,
   máx 8.47%) → línea base para alertas por desviación persistente.
9. **Ranking de puntos** del tablero top (MWh/mes): Data center UPS 6.50,
   TN-P-12 4.23, Hemodiálisis 1.44…
10–12. **Perfil horario por punto top** (promedio + P95 horario) con
   interpretación operativa: Data center plano ~9 kW 24/7; TN-P-12 con jornada
   5AM–7PM y base 3–4 kW fuera de horario.

### Convenciones de comunicación del reporte
- Cifras en MWh con 2 decimales y **soles abreviados** ("S/ 22.7 mil").
- Cada análisis cierra con **"Sugerencia:"** accionable en caja de color.
- **Preguntas abiertas al cliente** para validación operativa ("¿Hay cargas
  críticas que operan 24/7 en TGA (N)?", "¿Tienen horarios de encendido?").
- Notas metodológicas pequeñas ("P95 muestra los valores más altos alcanzados
  cada hora de cada día del mes").

---

## Lo que el agente YA puede hacer (verificado en DB)

- Todos los cálculos de las láminas son reproducibles con `readings_reading`:
  consumo base, variación diaria, ranking, perfiles horarios, % tiempo por
  rango, desbalance de corriente ((max−min fases)/promedio), P95 horario
  (`percentile_cont(0.95)`).
- Los umbrales propuestos son comparables contra lo YA configurado en
  `alerts_energythresholdprofile` (max_demand_kw, etc.) → el agente puede
  decir "tu umbral configurado es X y el patrón real sugiere Y".

## Patrones adoptados en el prompt del agente (sesión 3)

1. Consumo base nocturno con la definición exacta del reporte (00–08, 22–24).
2. Día alto/bajo/promedio + delta, con narrativa "día bajo = referencia".
3. "Otros no desagregados" = llave general − Σ hijos del mismo tablero.
4. Perfil horario promedio + P95 por punto (`percentile_cont`).
5. % de tiempo por rango de potencia → propuesta de umbrales en 3 niveles
   (caída/alta/crítica) con números concretos.
6. Desbalance de corriente % por hora (promedio y P95).
7. Formato KPI: MWh/kWh + S/ abreviados; cierre con sugerencia accionable;
   preguntas de validación operativa cuando falte contexto del negocio.

## ⚠️ Observaciones para el equipo (discrepancias detectadas)

1. **Tarifa implícita del reporte ≈ 0.155 S//kWh** (146.36 MWh → "S/ 22.7 mil";
   71.37 MWh → "S/ 11.1 mil"). La tabla `enterprises_billingdata` tiene
   0.65 S//kWh para los CLIENTES LIBRES y **Sanna no tiene tarifa registrada**
   (energyheadquarter.billing_data_id = NULL). ¿De dónde sale 0.155?
   ¿Es solo el cargo de energía sin fijos ni potencia? → Confirmar la tarifa
   real de Sanna y registrarla en la DB para que los S/ del agente sean exactos.
2. El "consumo base" del reporte (1,707 kWh/día = 35%) usa horas fijas; el
   agente puede ofrecer además la base medida como percentil (P10 horario),
   más robusta a cambios de horario.
3. Los umbrales propuestos (75/130/150 kW) parecen definidos a ojo con la
   distribución correcta; el agente puede generarlos de forma sistemática
   (mediana y P95 por tablero) y compararlos con los configurados.

## Oportunidades de producto identificadas

1. **"Genera el reporte mensual"**: el agente puede componer las secciones
   2–10 bajo demanda con cualquier sede/periodo disponible (los datos vivos
   solo cubren Sanna y Oechsle hoy).
2. **Alertas recomendadas con un clic**: del análisis de comportamiento a
   proponer umbrales listos para cargar en `alerts_alertthreshold`.
3. **Detección automática de "otros no desagregados" alto**: si lo no
   monitoreado supera X% del total, sugerir nuevos puntos de medición.
4. **Comparador de periodos**: mes vs mes anterior / mismo mes año anterior
   (cuando haya historia), hoy frecuente en preguntas de cliente.
5. **Watchlist de cargas 24/7**: puntos planos tipo Data center con su costo
   mensual en S/ → candidatos a eficiencia.
