"""Avisos automáticos por Telegram: backtest, un ciclo de prueba y estado.

    python scripts/avisos.py --backtest --desde 2026-09-23 --hasta 2026-10-20
    python scripts/avisos.py --ciclo     # un ciclo AHORA (simulado, estado en memoria)
    python scripts/avisos.py --estado    # lo mismo que /avisos en el grupo

Contra producción en vivo: scripts\\avisos.cmd <lo mismo> (mismas variables que
el bot). Nada de esto envía mensajes: el envío real lo hace el bot con
AVISOS_MODO=enviar. El backtest deja el detalle en analisis/ (gitignored).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import avisos as av  # noqa: E402
from src import avisos_vivo as vivo  # noqa: E402
from src import config, tunnel  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SALIDA = config.ROOT / "analisis"


def _fecha(m: av.Mensaje) -> str:
    t = av.lima(m.t)
    return f"{t.day:02d}-{av.MESES[t.month - 1]} {t:%H:%M}"


def _por_que(e, grupos: list, r: av.Reglas) -> str:
    """Por qué un incidente se cerró sin avisar (para revisar el backtest)."""
    inc = e.inc
    if inc.detalle.get("conocido"):
        return f"ya conocido ({inc.detalle['conocido']}) y duró menos de 6 h"
    k = inc.unidades[0] if inc.unidades else None
    for g in grupos:
        if k in g.inc.unidades and g.inc.abierto <= e.t and (g.inc.cerrado or e.t) >= inc.abierto:
            return f"dentro del corte de {g.inc.detalle.get('alcance')}"
    if inc.notificar_desde and e.t < inc.notificar_desde:
        return "recayó poco después de volver y se resolvió antes de 2 h"
    t, fuera = inc.abierto, True
    while t <= e.t:
        if av.en_horario(t, r):
            fuera = False
            break
        t += timedelta(minutes=5)
    if fuera:
        return "fuera de horario (sale en el resumen)"
    return "tapado por otro aviso (plataforma o base sin leer)"


def informe(res: dict, desde: date, hasta: date) -> tuple:
    """(resumen para la consola, detalle completo) del backtest."""
    msgs = res["mensajes"]
    h = res["historia"]
    r = res.get("reglas") or av.REGLAS
    dias = (hasta - desde).days + 1
    sonido = [m for m in msgs if not m.silencioso]
    por_nivel = Counter(m.nivel for m in sonido)
    abiertos = [e for e in res["eventos"] if e.tipo == "abre"]
    cierres = [e for e in res["eventos"] if e.tipo == "cierra"]
    callados = [e for e in cierres if not e.inc.avisado]
    grupos = [e for e in cierres if e.inc.tipo == "grupo"]
    mods = Counter(u.modulo for u in h.unidades.values()
                   if h.ultima(u.clave, h._t(h.n)) is not None)
    cab = [f"Backtest de avisos {desde} → {hasta} ({dias} días) · carga {res['carga_s']:.0f} s, "
           f"total {res['total_s']:.0f} s",
           "Unidades con datos: " + ", ".join(f"{n} {m}" for m, n in sorted(mods.items())),
           f"Mensajes: {len(msgs)} · con sonido {len(sonido)} "
           f"({', '.join(f'{av.ICONO[k]} {n}' for k, n in sorted(por_nivel.items()))}) · "
           f"sin sonido {len(msgs) - len(sonido)} "
           f"(cierres {sum(m.tipo == 'cierre' for m in msgs)}, "
           f"resúmenes {sum(m.tipo == 'resumen' for m in msgs)})",
           f"Con sonido por día: {len(sonido) / dias:.1f} en promedio, máximo "
           f"{max(Counter(av.lima(m.t).date() for m in sonido).values(), default=0)}",
           "Incidentes avisados por tipo: " + ", ".join(
               f"{k} {n}" for k, n in Counter(e.inc.tipo for e in abiertos).most_common()),
           f"Incidentes que se resolvieron sin avisar (de noche, tapados o en mantenimiento): "
           f"{len(callados)} (" + ", ".join(
               f"{k} {n}" for k, n in Counter(e.inc.tipo for e in callados).most_common()) + ")",
           "", "Por día (🔊 con sonido / 🔇 sin sonido):"]
    porfecha = {}
    for m in msgs:
        porfecha.setdefault(av.lima(m.t).date(), []).append(m)
    for d in sorted(porfecha):
        ms = porfecha[d]
        s = [m for m in ms if not m.silencioso]
        cab.append(f"  {av.DIAS_CORTO[d.weekday()]} {d.day:02d}-{av.MESES[d.month - 1]}: "
                   f"🔊 {len(s):2d} " + " ".join(av.ICONO[m.nivel] for m in s)
                   + f"  🔇 {len(ms) - len(s)}")
    detalle = cab + ["", "=" * 70, "Mensajes en orden (como llegarían al grupo):", ""]
    for m in msgs:
        marca = "🔇" if m.silencioso else "🔊"
        lineas = m.texto.splitlines() or [""]
        detalle.append(f"[{_fecha(m)}] {marca} {lineas[0]}")
        detalle += [f"{'':16}{x}" for x in lineas[1:]]
        detalle.append("")
    detalle += ["=" * 70, f"Incidentes que se cerraron SIN avisar ({len(callados)}):", ""]
    for e in sorted(callados, key=lambda e: e.inc.desde):
        i = e.inc
        dur = av.duracion((e.t - i.desde).total_seconds() / 60)
        detalle.append(f"[{_fecha(av.Mensaje('', '', True, i.desde))}] {i.tipo} · {i.nombre} "
                       f"({i.donde}) · {dur} · {_por_que(e, grupos, r)}")
    return "\n".join(cab), "\n".join(detalle)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    que = ap.add_mutually_exclusive_group(required=True)
    que.add_argument("--backtest", action="store_true", help="simular semanas pasadas")
    que.add_argument("--ciclo", action="store_true", help="un ciclo ahora (no envía)")
    que.add_argument("--estado", action="store_true", help="lo mismo que /avisos")
    ap.add_argument("--desde", type=date.fromisoformat)
    ap.add_argument("--hasta", type=date.fromisoformat)
    ap.add_argument("--paso", type=int, default=5, help="minutos entre evaluaciones (5)")
    args = ap.parse_args()
    try:
        if args.estado:
            print(vivo.texto_estado())
            return 0
        if args.ciclo:
            motor = vivo.Motor(modo="simular", store=vivo.Store(":memory:"), pausa_s=0,
                               log=lambda m: None)
            msgs = motor.ciclo()
            print(f"{len(msgs)} mensaje(s) que mandaría un primer ciclo ahora:\n")
            for m in msgs:
                print(("🔇 " if m.silencioso else "🔊 ") + m.texto + "\n")
            print(vivo.texto_estado(store=motor.store, modo="simular"))
            return 0
        hasta = args.hasta or (date.today() - timedelta(days=1))
        desde = args.desde or (hasta - timedelta(days=27))
        print(f"Cargando {desde - timedelta(days=7)} → {hasta} y simulando cada {args.paso} min…",
              flush=True)
        res = vivo.backtest(desde, hasta, paso_min=args.paso)
        corto, largo = informe(res, desde, hasta)
        SALIDA.mkdir(exist_ok=True)
        ruta = SALIDA / f"avisos_backtest_{desde}_{hasta}.txt"
        ruta.write_text(largo, encoding="utf-8")
        print(corto)
        print(f"\nDetalle (todos los mensajes): {ruta}")
        return 0
    finally:
        tunnel.close_tunnel()


if __name__ == "__main__":
    sys.exit(main())
