"""Genera panel.html con el estado de las dos colonias. Lo llaman los bots al terminar; también: python panel.py"""
import csv
import html
import re
import json
from datetime import datetime
from pathlib import Path

DIR = Path(__file__).parent
SALIDA = DIR / "panel.html"
HIST = DIR / "historial.csv"
CAPITAL = 50_000.0
REAL = 100.0

COLONIAS = [
    ("tendencia", "🐢 Colonia de tendencia", "colonia.json",
     "Autónomos: cada bot investiga ~140 ETF y acciones y elige dónde estar mientras el precio siga sobre su media. Decide cada día tras el cierre.",
     lambda b: f"media {b['media']} días · gusto {b.get('gusto', 'historia')}"),
    ("diaria", "⚡ Colonia diaria", "colonia_diaria.json",
     "Autónomos: cada 15 min investigan todo el universo (+ lo que más se mueve hoy), eligen dónde operar y cierran todo antes del cierre.",
     lambda b: f"{b['estrategia']} {b['p']}"),
    ("cripto", "🪙 Colonia cripto 24/7", "colonia_cripto.json",
     "Autónomos las 24 horas, todos los días: investigan las ~33 criptomonedas de Alpaca cada 15 min y eligen dónde estar (stop 3 %).",
     lambda b: f"{b['estrategia']} {b['p']} h"),
]
GEMELOS = {"QQQ": "EQQQ", "SPY": "SXR8", "GLD": "4GLD", "QQQM": "EQQQ", "VOO": "SXR8", "IAU": "4GLD"}
NOMBRE = {"QQQ": "Nasdaq 100", "QQQM": "Nasdaq 100", "SPY": "S&P 500", "VOO": "S&P 500", "GLD": "Oro", "IAU": "Oro"}


def cargar(fichero, modulo):
    p = DIR / fichero
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    try:  # aún no ha operado: mostrar los fundadores
        return __import__(modulo).cargar_estado()
    except Exception:
        return None


def calc(estado):
    precios = estado.get("precios", {})
    fx = estado.get("eurusd", 1.08)
    fx0 = estado.get("eurusd_inicio", fx)
    a100 = lambda usd: usd / fx / (CAPITAL / fx0) * REAL
    for b in estado["bots"]:
        b["_valor"] = b["efectivo"] + b["acciones"] * precios.get(b["activo"], 0)
        b["_eur"] = a100(b["_valor"])
        b["_cambio"] = b["_valor"] / b["referencia"] - 1 if b["estado"] != "muerto" else -1
        ini = b.get("inicial", b["referencia"])
        b["_ini_eur"] = a100(ini)
        b["_pyg_eur"] = (b["_eur"] if b["estado"] != "muerto" else 0) - b["_ini_eur"]
        b["_pyg_pct"] = b["_pyg_eur"] / b["_ini_eur"] if b["_ini_eur"] else 0
        b["_hijo_eur"] = a100(b["referencia"] * 1.20) - b["_eur"]
        b["_muerte_eur"] = b["_eur"] - a100(b["referencia"] * 0.80)
        b["_pos_eur"] = a100(b["acciones"] * precios.get(b["activo"], 0))
    vivos = [b for b in estado["bots"] if b["estado"] != "muerto"]
    total = sum(b["_valor"] for b in vivos) + estado.get("reserva", 0)
    return vivos, total, a100(total), a100(estado.get("reserva", 0))


def guardar_historial(datos):
    hoy = datetime.now().strftime("%Y-%m-%d")
    filas = []
    if HIST.exists():
        filas = [r for r in csv.DictReader(HIST.open(encoding="utf-8")) if r["fecha"] != hoy]
    for clave, eur in datos.items():
        filas.append({"fecha": hoy, "colonia": clave, "eur": f"{eur:.2f}"})
    with HIST.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["fecha", "colonia", "eur"])
        w.writeheader()
        w.writerows(filas)
    return filas


def grafica(filas):
    series = {}
    for r in filas:
        series.setdefault(r["colonia"], []).append((r["fecha"], float(r["eur"])))
    fechas = sorted({r["fecha"] for r in filas})
    if len(fechas) < 2:
        return '<p class="nota">La gráfica aparecerá cuando haya al menos 2 días de datos.</p>'
    vals = [v for s in series.values() for _, v in s] + [REAL]
    lo, hi = min(vals) - 1, max(vals) + 1
    W, H, P = 640, 220, 36
    x = lambda f: P + (W - 2 * P) * fechas.index(f) / (len(fechas) - 1)
    y = lambda v: H - P - (H - 2 * P) * (v - lo) / (hi - lo)
    partes = [f'<line x1="{P}" x2="{W-P}" y1="{y(REAL):.1f}" y2="{y(REAL):.1f}" class="base"/>',
              f'<text x="{P-4}" y="{y(REAL)+4:.1f}" class="eje" text-anchor="end">100 €</text>']
    for clave, s in series.items():
        pts = " ".join(f"{x(f):.1f},{y(v):.1f}" for f, v in s)
        partes.append(f'<polyline points="{pts}" class="linea {clave}"/>')
        f, v = s[-1]
        partes.append(f'<text x="{x(f)+6:.1f}" y="{y(v)+4:.1f}" class="et {clave}">{v:.2f} €</text>')
    partes.append(f'<text x="{P}" y="{H-8}" class="eje">{fechas[0]}</text>')
    partes.append(f'<text x="{W-P}" y="{H-8}" class="eje" text-anchor="end">{fechas[-1]}</text>')
    return f'<svg viewBox="0 0 {W+40} {H}" class="graf" role="img" aria-label="Evolución de las colonias">{"".join(partes)}</svg>'


def tarjeta_bot(b, desc, hijos, nivel):
    est = b["estado"]
    icono = {"vivo": "🟢", "herido": "🟠", "muerto": "💀"}[est]
    dentro = b["acciones"] > 0
    cambio = b["_cambio"]
    pcls = "pos" if b["_pyg_eur"] > 0.005 else "neg" if b["_pyg_eur"] < -0.005 else ""
    html_hijos = "".join(tarjeta_bot(h, desc, hijos, nivel + 1) for h in hijos.get(b["id"], []))
    return f"""
    <div class="bot {est}" style="margin-left:{min(nivel, 4) * 18}px">
      <div class="cab"><span class="ico">{icono}</span><b>Bot {b.get('_pref', '')}{b['id']}</b>
        <span class="tag">{(NOMBRE.get(b['activo'], b['activo']) + ' · ' + b['activo'] + (' → ' + GEMELOS[b['activo']] if b['activo'] in GEMELOS else '')) if b.get('activo') else '🔎 buscando'}</span>
        <span class="tag suave">{html.escape(desc(b))}</span>
        <span class="tag {'dentro' if dentro else 'fuera'}">{'DENTRO' if dentro else 'FUERA'}</span></div>
      <div class="fila">
        <div class="dato"><small>Tiene ahora</small><span class="grande">{b['_eur']:.2f} €</span></div>
        <div class="dato"><small>Ganancia / pérdida</small><span class="grande {pcls}">{b['_pyg_eur']:+.2f} €</span><span class="{pcls}">{b['_pyg_pct']:+.1%}</span></div>
        <div class="dato"><small>Empezó con</small><span>{b['_ini_eur']:.2f} €</span></div>
        <div class="dato"><small>Qué está haciendo</small><span>{('Invertido: ' + f"{b['_pos_eur']:.2f} € en " + NOMBRE.get(b['activo'], str(b['activo']))) if dentro else 'Esperando en efectivo'}</span></div>
      </div>
      {('<div class="saber">👶 Le faltan <b>' + f"{max(0, b['_hijo_eur']):.2f} €" + '</b> para tener un hijo · 💀 Puede perder <b>' + f"{max(0, b['_muerte_eur']):.2f} €" + '</b> antes de morir' + (' · 🟠 Herido: invierte la mitad' if est == 'herido' else '') + '</div>') if est != 'muerto' else '<div class="saber">Murió. Su dinero pasó a la reserva.</div>'}
      <div class="suave peq">{b['_valor']:,.0f} $ ficticios · nacido {b['nacido']}{' · ' + str(b.get('hijos', 0)) + ' hijos' if b.get('hijos') else ''}{' · hijo del Bot ' + b.get('_pref', '') + str(b['padre']) if b['padre'] else ' · fundador'}</div>
      {('<div class="barra"><i style="width:' + f"{max(0, min(100, (cambio + 0.2) / 0.4 * 100)):.0f}" + '%"></i></div>') if est != 'muerto' else ''}
    </div>{html_hijos}"""


def generar():
    bloques, resumen = [], {}
    for clave, titulo, fichero, explica, desc in COLONIAS:
        estado = cargar(fichero, {"tendencia": "bot_alpaca", "diaria": "bot_diario", "cripto": "bot_cripto"}[clave])
        if not estado:
            continue
        vivos, total, eur, reserva = calc(estado)
        for b in estado["bots"]:
            b["_pref"] = {"diaria": "D", "cripto": "C"}.get(clave, "")
        resumen[clave] = eur
        muertos = len(estado["bots"]) - len(vivos)
        hijos = {}
        for b in estado["bots"]:
            hijos.setdefault(b["padre"], []).append(b)
        arbol = "".join(tarjeta_bot(b, desc, hijos, 0) for b in hijos.get(None, []))
        cls = "pos" if eur > REAL else "neg" if eur < REAL else ""
        bloques.append(f"""
  <section class="colonia">
    <h2>{titulo}</h2><p class="nota">{explica}</p>
    <div class="kpis">
      <div><small>Con 100 € reales</small><span class="grande {cls}">{eur:.2f} €</span><span class="{cls}">{eur-REAL:+.2f} € ({eur/REAL-1:+.1%})</span></div>
      <div><small>Vivos</small><span class="grande">{len(vivos)}</span></div>
      <div><small>Muertos</small><span class="grande">{muertos}</span></div>
      <div><small>Reserva</small><span class="grande">{reserva:.2f} €</span></div>
    </div>
    {arbol}
  </section>""")
    filas = guardar_historial(resumen)
    log = operaciones = ""
    if (DIR / "registro.log").exists():
        todas = (DIR / "registro.log").read_text(encoding="utf-8").splitlines()
        real = re.compile(r"Orden enviada|🛒 COMPRA| COMPRA | VENDE |STOP:|OBJETIVO:|cerrado por|MUERTO|HIJO|no se llenó")
        # antes del 9-nov solo hubo pruebas (órdenes canceladas): no cuentan
        ops = [l for l in todas if real.search(l) and "[PRUEBA]" not in l and l[:10] >= "2026-11-09"][-25:]
        resto = [l for l in todas if l not in ops][-25:]
        operaciones = "\n".join(html.escape(l) for l in reversed(ops)) or "Todavía ningún bot ha comprado ni vendido nada."
        log = "\n".join(html.escape(l) for l in reversed(resto))
    pagina = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>Mis bots</title><style>
:root{{--bg:#f6f5f2;--card:#fff;--tx:#1d1d1b;--mut:#6b6a66;--ln:#e4e2dc;--pos:#1a7f4b;--neg:#c0392b;--amb:#c77700;--t:#2f6fdf;--d:#8e44ad;--c:#b7791f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1d;--tx:#eceae4;--mut:#9a988f;--ln:#33322f;--pos:#4cc38a;--neg:#ff6b5b;--amb:#f0a63a;--t:#6c9cff;--d:#c58af0;--c:#e5b84b}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:900px;margin:auto;padding:20px 16px 60px}}h1{{margin:0 0 4px;font-size:26px}}h2{{margin:0;font-size:20px}}
.nota,.suave{{color:var(--mut)}}.nota{{margin:4px 0 14px;font-size:14px}}
.colonia,.caja{{background:var(--card);border:1px solid var(--ln);border-radius:14px;padding:18px;margin-top:18px}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}}.kpis div{{border:1px solid var(--ln);border-radius:10px;padding:10px}}
.kpis div{{display:flex;flex-direction:column}}.kpis small{{display:block;color:var(--mut);font-size:12px}}.grande{{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}}
.bot{{border:1px solid var(--ln);border-left:4px solid var(--pos);border-radius:10px;padding:10px 12px;margin-top:8px}}
.bot.herido{{border-left-color:var(--amb)}}.bot.muerto{{border-left-color:var(--mut);opacity:.55}}
.cab{{display:flex;flex-wrap:wrap;gap:6px;align-items:center}}.tag{{font-size:12px;border:1px solid var(--ln);border-radius:20px;padding:1px 8px}}
.tag.dentro{{color:var(--pos);border-color:var(--pos)}}.tag.fuera{{color:var(--mut)}}
.nums{{display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;margin-top:6px}}.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}
.fila{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:8px}}.dato{{display:flex;flex-direction:column}}.dato small{{color:var(--mut);font-size:12px}}
.saber{{font-size:13px;margin-top:8px;background:var(--bg);border-radius:8px;padding:6px 10px}}.peq{{font-size:12px;margin-top:6px}}
@media (max-width:600px){{.fila{{grid-template-columns:repeat(2,1fr)}}}}
.barra{{height:5px;background:var(--ln);border-radius:4px;margin-top:8px;position:relative}}.barra i{{display:block;height:100%;border-radius:4px;background:linear-gradient(90deg,var(--neg),var(--amb) 25%,var(--pos) 50%,var(--pos))}}
.barra:after{{content:"";position:absolute;left:50%;top:-3px;height:11px;border-left:1px dashed var(--mut)}}
.leyenda{{font-size:12px;color:var(--mut);display:flex;justify-content:space-between;margin-top:2px}}
.graf{{width:100%;height:auto}}.graf .linea{{fill:none;stroke-width:2.5}}.graf .tendencia{{stroke:var(--t);fill:var(--t)}}.graf .diaria{{stroke:var(--d);fill:var(--d)}}.graf .cripto{{stroke:var(--c);fill:var(--c)}}
.graf polyline.tendencia,.graf polyline.diaria,.graf polyline.cripto{{fill:none}}.graf .base{{stroke:var(--mut);stroke-dasharray:4 4}}.graf .eje,.graf .et{{font-size:12px;fill:var(--mut)}}
.graf text.tendencia{{fill:var(--t)}}.graf text.diaria{{fill:var(--d)}}.graf text.cripto{{fill:var(--c)}}
pre.ops{{color:var(--tx);font-size:13px}}pre{{white-space:pre-wrap;font-size:12px;color:var(--mut);max-height:320px;overflow:auto;margin:0}}
@media (max-width:600px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main>
<h1>🤖 Mis colonias de bots</h1>
<p class="nota">Dinero falso (Alpaca Paper) · cada colonia representa 100 € reales · actualizado {datetime.now():%d/%m/%Y %H:%M} · la página se recarga sola cada 5 min</p>
<div class="caja"><h2>Evolución (con 100 € reales)</h2>
<p class="nota"><span style="color:var(--t)">━ Tendencia</span> &nbsp; <span style="color:var(--d)">━ Diaria</span> &nbsp; <span style="color:var(--c)">━ Cripto</span> &nbsp; - - 100 € de partida</p>{grafica(filas)}</div>
{''.join(bloques)}
<p class="nota" style="margin-top:10px">Barra de vida: izquierda = muerte (-20 %), centro = punto de partida, derecha = hijo (+20 %). 🟢 vivo · 🟠 herido · 💀 muerto</p>
<div class="caja"><h2>💸 Operaciones de verdad</h2><p class="nota">Compras, ventas, stops, objetivos, nacimientos y muertes: lo único que mueve el dinero.</p><pre class="ops">{operaciones}</pre></div>
<div class="caja"><h2>🔍 Lo que han estudiado (sin operar)</h2><p class="nota">Ideas que los bots miraron y descartaron (❌ no) y avisos. Esto NO mueve el dinero.</p><pre>{log}</pre></div>
</main></body></html>"""
    SALIDA.write_text(pagina, encoding="utf-8")
    return SALIDA


if __name__ == "__main__":
    print(generar())
