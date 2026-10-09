# Respuesta al jefe — puntos sin lectura (data al 09-oct-2026)

Fuente: DB `energy` local (sync full 09-oct-2026) + exports regenerados hoy.
Queries: inventario (`measurementpoint` + `device`) cruzado con agregados de
`readings_reading` por punto. Solo lectura, sin cambios en la DB.

## Resumen ejecutivo

**Corrección de conteos**: el reporte decía 20 Oechsle + 11 Sanna, pero en
plataforma hay **22 + 13**. La diferencia es un bug del reporte (colapsaba
nombres duplicados: "Reserva" ×2 en Oechsle; "Espacio monofásico" ×2 y
"Otros" ×2 en Sanna). Ya corregido en el formato nuevo.

**Los 35 puntos (22 Oechsle + 13 Sanna) están en el mismo estado:
circuito creado en plataforma, SIN sensor real, SIN una sola lectura.**
Todos tienen `is_active=false`, dispositivo de nombre `Device falso/false *`
con `is_active=false`, y **0 lecturas en 11.5M de filas**. Creados el
29-dic-2025 (Sanna) y 09/18-feb-2026 (Oechsle). No es una caída: nunca
enviaron nada. Ninguno requiere visita de campo por "sensor caído";
la pregunta pendiente es solo si siguen figurando como alcance o se dan de
baja en plataforma (pregunta 3).

**Los puntos que SÍ tuvieron sensor y se cayeron** (Burger King, KFC,
Madam Tusan, Pizza Hut) salen del reporte con la limpieza pedida; sus
fechas de caída quedan registradas abajo para constancia.

## 1. Oechsle Salaverry — 22 puntos, todos sin sensor (estado B)

| id | Tablero | Punto | Dispositivo | Lecturas | Creado |
|---|---|---|---|---|---|
| 85 | Tableros TGE-TR1 | TDE-A1 (Alumbrado emergencia primer piso) | Device falso …ab81a (inactivo) | 0 | 09-feb-2026 |
| 86 | Tableros TGE-TR1 | TDE-A2 (Alumbrado emergencia segundo piso) | Device falso …ab81a (inactivo) | 0 | 09-feb-2026 |
| 87 | Tableros TGE-TR1 | TDE-A3 (Alumbrado emergencia tercer piso) | Device falso …ab81a (inactivo) | 0 | 09-feb-2026 |
| 88 | Tableros TGE-TR1 | TDE-A4 (Alumbrado emergencia cuarto piso) | Device falso …ab81a (inactivo) | 0 | 09-feb-2026 |
| 89 | Tableros TGE-TR1 | TD-UPS1 | Device falso …ab81a (inactivo) | 0 | 09-feb-2026 |
| 90 | Tableros TGE-TR1 | TD-UPS2 | Device falso …ab81a (inactivo) | 0 | 09-feb-2026 |
| 91 | Tableros TGE-TR1 | TDF-AAE | Device falso …ab81a (inactivo) | 0 | 09-feb-2026 |
| 92 | Tableros TGE-TR1 | Tablero TD-CR1 | Device falso …ab81a (inactivo) | 0 | 09-feb-2026 |
| 93 | Tableros TGE-TR1 | Tablero TD-CR2 | Device falso …ab81a (inactivo) | 0 | 09-feb-2026 |
| 94 | Tableros TGE-TR1 | Tablero Sotano | Device falso …f402360 (inactivo) | 0 | 09-feb-2026 |
| 95 | Tableros TGE-TR1 | Reserva | Device falso …f402360 (inactivo) | 0 | 09-feb-2026 |
| 96 | Tableros TGE-TR1 | Interruptor de potencia | Device falso …f402360 (inactivo) | 0 | 09-feb-2026 |
| 103 | Tableros TGE-TR1 | Alumbrado cuarto de tableros | Device falso …f402360 (inactivo) | 0 | 18-feb-2026 |
| 104 | Tableros TGE-TR1 | Tomacorriente cuarto de tableros | Device falso …f402360 (inactivo) | 0 | 18-feb-2026 |
| 97 | Tableros TG-TR1 Iluminación | Tablero TD-T1 (Tomacorriente 1º y 2º piso) | Device false …f402351 (inactivo) | 0 | 09-feb-2026 |
| 98 | Tableros TG-TR1 Iluminación | Tablero TD-T2 (Tomacorriente tercer piso) | Device false …f402351 (inactivo) | 0 | 09-feb-2026 |
| 99 | Tableros TG-TR1 Iluminación | Tablero TD-T3 (Tomacorriente tercer piso) | Device false …f402351 (inactivo) | 0 | 09-feb-2026 |
| 100 | Tableros TG-TR1 Iluminación | Tablero TD-TFIN | Device false …f402351 (inactivo) | 0 | 09-feb-2026 |
| 101 | Tableros TG-TR1 Iluminación | Cargador de bateria grupo electrogeno | Device falso …00ab9b5 (inactivo) | 0 | 09-feb-2026 |
| 102 | Tableros TG-TR1 Iluminación | Resistencia calefactora | Device falso …00ab9b5 (inactivo) | 0 | 09-feb-2026 |
| 105 | Tableros TG-TR1 Iluminación | Reserva | Device falso …00ab9b5 (inactivo) | 0 | 18-feb-2026 |
| 106 | Tableros TG-TR1 Iluminación | Reserva | Device falso …00ab9b5 (inactivo) | 0 | 18-feb-2026 |

Veredicto: **solo creados en plataforma, sin sensor instalado**. Todos con
`is_active=false` en punto y dispositivo.

## 2. Sanna San Borja — 13 puntos, todos sin sensor (estado B)

| id | Tablero | Punto | Dispositivo | Lecturas | Creado |
|---|---|---|---|---|---|
| 45 | TFC-BAF (Bomba agua fría) | Arrancador Bomba 1 - 15HP | Device Falso …210952 (inactivo) | 0 | 29-dic-2025 |
| 46 | TFC-BAF (Bomba agua fría) | Arrancador Bomba 2 - 15HP | Device Falso …210952 (inactivo) | 0 | 29-dic-2025 |
| 47 | TFC-BAF (Bomba agua fría) | PLC | Device Falso …210952 (inactivo) | 0 | 29-dic-2025 |
| 48 | TFC-BAF (Bomba agua fría) | Arrancador Bomba 3 - Reserva equipada | Device Falso …210952 (inactivo) | 0 | 29-dic-2025 |
| 49 | TFC-BAF (Bomba agua fría) | Espacio monofasico | Device Falso …210952 (inactivo) | 0 | 29-dic-2025 |
| 50 | TFC-BAF (Bomba agua fría) | Espacio monofasico | Device Falso …210952 (inactivo) | 0 | 29-dic-2025 |
| 43 | Tablero General TG-P1 | Otros | Device Falso …004f73e (inactivo) | 0 | 29-dic-2025 |
| 44 | Tablero General TG-RT | Otros | Device Falso …00506c8 (inactivo) | 0 | 29-dic-2025 |
| 51 | Tablero TGA (N) | TE-P7.1 | Device Falso …00506F7 (inactivo) | 0 | 29-dic-2025 |
| 52 | Tablero TGA (N) | No identificados | Device Falso …00506F7 (inactivo) | 0 | 29-dic-2025 |
| 53 | Tablero TGA (N) | Mamografia - Semisotano | Device Falso …00506F7 (inactivo) | 0 | 29-dic-2025 |
| 54 | Tablero TGA (N) | TE-S 1.4 | Device Falso …00506F7 (inactivo) | 0 | 29-dic-2025 |
| 55 | Tablero TGA (N) | Bomba de vacio | Device Falso …00506F7 (inactivo) | 0 | 29-dic-2025 |

Veredicto: **solo creados en plataforma, sin sensor instalado**. Los nombres
("No identificados", "Otros" ×2, "PLC", "Espacio monofasico" ×2) sugieren
placeholders que nunca entraron al alcance. **Falta confirmación del
responsable de contrato**: ¿se dan de baja en plataforma o quedan como
reserva? (pregunta 3 del jefe — la DB no puede responderla).

## 3. Puntos con sensor real que dejaron de enviar (salen del reporte)

| Punto | Sensor | Lecturas | Envió desde → hasta |
|---|---|---|---|
| Burger King/Salaverry · Tablero General TG-1 (id 108) | Analizador trifásico 600A, ADW210 (activo) | 60,171 | 28-feb-2026 → **31-jul-2026** |
| KFC/Salaverry · Tablero General TG-1 (id 109) | Analizador trifásico 600A, ADW210 (inactivo) | 45,611 | 03-mar-2026 → **11-abr-2026** |
| Madam Tusan · Tablero General TG-1 (id 166) | Analizador trifásico 4000A, ADW300 (activo) | 8,712 | 20-abr-2026 → **14-may-2026** |
| Madam Tusan · TABLERO DE COCINA T-C (id 167) | Analizador trifásico 600A, ADW210 (activo) | 9,049 | 20-abr-2026 → **14-may-2026** |
| Pizza Hut/Salaverry · Tablero General TG-1 (id 107) | ADW210 (inactivo) | 63,948 | 25-feb-2026 → **20-abr-2026** |

TestCorp_Email ("Sede Email Test", punto PM-Email): empresa de pruebas,
0 lecturas. También fuera del reporte.

## Notas metodológicas

- El nombre "Device falso" **solo** no basta como prueba: el punto 107
  (Pizza Hut TG-1) tiene un device llamado "falso" pero transmitió 63,948
  lecturas durante 2 meses. La clasificación usa nombre + 0 lecturas +
  punto inactivo + dispositivo inactivo en conjunto.
- `installation_date` está en NULL en los 40 puntos (columna sin uso en la
  práctica); no sirve como evidencia de instalación.
- La cadencia se contó sobre `"EPpos_value" IS NOT NULL` (el contador
  confiable), igual que el pipeline de cobertura.
