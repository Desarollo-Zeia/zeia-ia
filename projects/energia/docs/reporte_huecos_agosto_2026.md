# Reporte de huecos en lecturas de energía — agosto 2026

**Fecha de generación**: 2026-08-21
**Periodo analizado**: 2026-08-01 al 2026-08-20 (hora Lima). Los datos locales
terminan el **17-ago 14:33 Lima** (sincronización pendiente: 18–20 sin lecturas).
**Método**: hueco = intervalo entre lecturas consecutivas de un mismo punto
> 2 min (cadencia normal ≈ 62 s). Evento = agrupación de huecos con pausa
≤ 10 min entre ellos. Umbral de 2 min elimina el ruido de la cadencia;
micro-huecos de 1–2 min no se reportan (≈1 lectura).
**Cobertura**: 31 puntos de medición activos (Sanna San Borja + Oechsle Salaverry).
~713.7k lecturas en el periodo; **11,384 lecturas faltantes ≈ 1.6 %**.

## Resumen por sitio

| Empresa | Sede | Eventos | Horas sin datos | Lecturas faltantes |
|---|---|---|---|---|
| Oechsle | Salaverry | 1,855 | **186.6 h** | 6,545 |
| Sanna | San Borja | 2,488 | **144.2 h** | 4,839 |
| **Total** | | **4,343** | **330.8 h** | **11,384** |

La mayoría de los 4,343 eventos son micro-pérdidas de 2–10 min (padrón
intermitente de los dispositivos, no caídas de sitio). Los hits grandes
(> 5 h) son pocos y puntuales (ver abajo).

## Top eventos (más horas sin datos)

| Fecha | Sitio | Tablero / punto | Duración | Huecos | Mayor hueco |
|---|---|---|---|---|---|
| 06-ago | Oechsle Salaverry | TG-TR2 (TF-AA) | **28.6 h** | 536 | 9.3 min |
| 15-16-ago | Oechsle Salaverry | Chiller 2 | 13.1 h | 271 | 7.2 min |
| 02-ago | Oechsle Salaverry | Llave general TG-TR1 | 12.4 h | 252 | 7.2 min |
| 16-17-ago | Oechsle Salaverry | Chiller 2 | 10.0 h | 206 | 7.2 min |
| 12-ago | Oechsle Salaverry | Chiller 2 | 9.9 h | 200 | 7.2 min |
| 15-ago | Oechsle Salaverry | Red de emergencia | 7.5 h | 152 | 6.2 min |
| 06-07-ago | Sanna San Borja | Tomógrafo (TG-RT) | 7.2 h | 128 | 8.3 min |
| 02-03-ago | Oechsle Salaverry | Llave general TG-TR1 | 6.5 h | 131 | 8.3 min |
| 11-12-ago | Oechsle Salaverry | Chiller 2 | 5.5 h | 110 | 7.2 min |
| 12-ago | Sanna San Borja | Resonador (TGA-N) | 5.2 h | 85 | 9.3 min |

**Patrón intermitente (no caída única)**: los eventos grandes tienen cientos
de huecos con el mayor de ~7–9 min → dispositivos que se caen y se recuperan
repetidas veces, no un corte de energía.

## Días más críticos (horas sin datos, ambos sitios)

| Fecha | Horas | Eventos |
|---|---|---|
| 06-ago | 46.4 | 265 |
| 12-ago | 34.2 | 269 |
| 15-ago | 31.3 | 263 |
| 02-ago | 28.4 | 238 |
| 04-ago | 22.6 | 279 |
| 16-ago | 20.2 | 242 |
| 11-ago | 19.5 | 244 |

## Top puntos con más pérdida (agosto 01–17)

| Sitio | Tablero / punto | Eventos | Horas | Lecturas faltantes |
|---|---|---|---|---|
| Oechsle | TG-TR2 (TF-AA) HVAC — Chiller 2 | 33 | **46.7 h** | 1,735 |
| Oechsle | TG-TR2 (TF-AA) HVAC — TG-TR2 | 38 | 34.2 h | 1,301 |
| Sanna | TG-RT — Tomógrafo | 274 | 28.3 h | 997 |
| Oechsle | TG-TR1 Iluminación — Llave general | 48 | 27.7 h | 1,005 |
| Sanna | TFC-BAF — Llave general cuarto de bombas | 355 | 26.6 h | 911 |
| Sanna | TGA (N) — Resonador | 205 | 21.8 h | 816 |
| Oechsle | TGE-TR1 — Tablero TD-FE | 406 | 16.9 h | 519 |
| Sanna | TGA (N) — RX-PB | 367 | 15.4 h | 480 |

## Conclusiones y acciones sugeridas

1. **Calidad del monitoreo aceptable**: ~1.6 % de lecturas perdidas en 17
   días; ningún punto se fue completamente sin datos por periodo extenso.
2. **Oechsle Salaverry** es donde más se pierde (>50 % del total), liderado
   por el tablero HVAC **TG-TR2 (Chiller 2)** 05–17-ago: 46.7 h pérdidas,
   puntas de 10–13 h. Revisar el ADW300/dispositivo del Chiller 2 (posibles
   CT sueltos/sobrecalentamiento). Importante porque es uno de los 2 puntos
   que definen el total de la sede (criterio del cliente, sesión 8).
3. **Sanna San Borja** tiene muchos eventos pequeños (2,488) pero de baja
   duración: pérdidas intermitentes de ~3.3 min en Tomógrafo, cuarto de
   bombas, TGA — probable tolencia de red wifi/LoRa, no corte eléctrico.
4. **Sincronizar la DB local** para cerrar los 18–20-ago antes de sacar
   conclusiones semanales.

## Archivos generados

- `analisis/huecos_2026-08-01_a_2026-08-21.csv` (7,854 huecos, Excel OK)
- `analisis/eventos_2026-08-01_a_2026-08-21.csv` (4,343 eventos)
- Tablas `analisis_huecos` / `analisis_eventos` en la DB local
- Dashboard: `python webapp.py` → `http://localhost:8000/gaps`
