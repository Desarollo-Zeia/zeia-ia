# Análisis de huecos (pérdidas de lecturas) — guía de reproducción

Genera un reporte de **cada interrupción en las lecturas de energía** de los
puntos de medición, y la agrupa en **eventos** para detectar accidentes
grandes que no se ven hueco por hueco.

## Qué mide

- **Cadencia normal**: 1 lectura/minuto por punto (intervalo real 60–62 s
  según el dispositivo). Un día normal deja ~46 min "vacíos" por esa
  cadencia: eso **no es pérdida**.
- **Hueco**: rango sin lecturas **> 2 min** entre dos lecturas consecutivas
  del mismo punto (equivale a ≥ 2 lecturas seguidas perdidas).
- **Evento**: unión de huecos del mismo punto cuando entre el fin de uno y el
  inicio del siguiente hubo **≤ 10 min** de lecturas normales. Un día con 536
  huecos intermitentes queda como 1 evento con sus totales.

## Salidas del reporte

| Salida | Dónde | Contenido |
|---|---|---|
| Tabla `analisis_huecos` | DB local (`energy`, schema public) | Cada hueco: fecha, inicio, fin, duración, lecturas faltantes |
| Tabla `analisis_eventos` | DB local (`energy`, schema public) | Cada accidente: ventana inicio–fin, horas sin datos, nº de huecos, mayor hueco |
| CSV huecos | `analisis/huecos_<inicio>_a_<fin>.csv` | Igual que la tabla, para Excel |
| CSV eventos | `analisis/eventos_<inicio>_a_<fin>.csv` | Igual que la tabla, para Excel |
| Página web | `http://localhost:8000/gaps` | Misma información con filtros y modo huecos/eventos |

## Requisitos

- Python 3.9 + venv del proyecto, PostgreSQL local con la base `energy`
  restaurada (más datos nuevas, mejor).
- Webapp para la vista del dashboard: `python webapp.py` → `:8000`.

## Paso a paso

### 1. Correr el análisis

```bash
source venv/bin/activate
PYTHONPATH=. python scripts/analisis_huecos.py
```

Con la configuración por defecto analiza **agosto 2026** (del 1 al día
actual, ambos en hora Lima) y registra huecos > 2 min agrupados con pausa
máxima de 10 min. Tarda un par de minutos (recorre ~1.5M de lecturas/mes).

### 2. Revisar los resultados

- Terminal: el script imprime nº de huecos, nº de eventos y rutas de los CSV.
- Dashboard: `http://localhost:8000/gaps` (reiniciar la webapp si estaba
  corriendo para tomar los últimos datos: `pkill -f webapp.py` y volver a
  `python webapp.py`).
- Excel: abrir los CSV (UTF-8 con BOM, los acentos se ven bien).

### 3. Interpretar (qué mirar primero)

1. **Modo "Eventos agrupados"** del dashboard, ordenados por duración: los
   accidentes grandes aparecen arriba. Columna clave: **Sin datos** (horas
   acumuladas sin registro dentro de la ventana inicio–fin).
2. Un evento con `n_huecos` alto y "mayor hueco" pequeño = dispositivo
   **intermitente** (se cae y vuelve repetido, nunca una caída única larga).
3. Un evento con `n_huecos = 1` = caída única y continua.
4. Cruzar varios puntos de la misma sede el mismo día = posible **caída de
   sitio** (gateway/energía).
5. Fechas con pérdida: restar día completo (1440 min) menos lecturas; el
   ~3.3% del día es cadencia normal.

## Opciones del script

```bash
# Otro rango de fechas (fin es exclusivo)
python scripts/analisis_huecos.py --inicio 2026-07-01 --fin 2026-07-31

# Huecos más largos únicamente (ignora los de ~2-3 min)
python scripts/analisis_huecos.py --min-hueco 10

# Agrupar eventos con más/menos tolerancia (pausa entre huecos)
python scripts/analisis_huecos.py --merge-max 5     # más eventos pequeños
python scripts/analisis_huecos.py --merge-max 30    # une ráfagas lejanas
```

## Detalles técnicos

- **Definición de hueco** (SQL): `EXTRACT(EPOCH FROM (created_at - lag(...)))/60`
  entre lecturas consecutivas del punto, filtradas a `> min_hueco`.
  Los timestamps se tratan en `America/Lima` (la columna `created_at` es UTC).
- **Regla de evento** (`agrupar_eventos`): huecos ordenados por punto y hora;
  se une al evento actual si la pausa desde el fin del evento anterior es
  `<= merge_max`; si no, el evento se cierra y abre uno nuevo. El `fin` del
  evento = instante de la última lectura tras la última caída que calificó.
- **Lecturas faltantes** por hueco = `floor(duración_min - 1)` (a cadencia de
  1/min).
- Las tablas se **recrean** en cada corrida (DROP + CREATE + INSERT): el
  reporte siempre refleja el rango pedido, no acumula corridas.

## Límites del método

- Un evento activo al final del análisis (dispositivo aún inestable) queda
  "cortado": su fin real no ha ocurrido todavía.
- Con datos locales hasta 18-ago-2026, los rangos posteriores no tienen
  lecturas: la caída mostraría "0" huecos, no error. Sincronizar primero con
  `scripts/sync_db.sh` si se quiere un rango más reciente.
- El umbral de 2 min es una convención: elimina el ruido de la cadencia de
  62 s pero omite micro-huecos de 1–2 min (solo se pierde ~1 lectura).