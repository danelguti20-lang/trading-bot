"""Colonia de bots (cuenta PAPER de Alpaca).

La colonia tiene 50.000 $ = 100 € reales x500 (mismas reglas que con 100 € en Trading 212 con ETF UCITS: fracciones de acción y comisión 0,25 %).
Cada bot tiene su propio dinero virtual ("vida") y una estrategia sencilla:
  estar invertido en una acción/ETF mientras su precio esté por encima de SU media de N días.
  AUTÓNOMOS: ningún bot tiene un activo fijo. Cuando está en efectivo investiga todo el universo
  (universo.py: ~140 ETF y acciones), mira qué valores están por encima de su media, estudia la
  historia de su regla en cada uno (6 años, con comisiones) y elige según su "gusto":
    historia = el que más ganó de media con su regla | fuerza = el que más ha subido en 6 meses
    seguro   = el que más veces ganó con su regla
  Antes de entrar mira noticias (descarta halt/legal/regulatory/risk). Prefiere no repetir lo que ya
  tiene un hermano. Se queda hasta que el precio cae bajo su media; entonces vende y vuelve a buscar.
Reglas de vida:
  - HERIDO  si su dinero cae un 10 % desde su punto de referencia -> invierte la mitad.
  - MUERTO  si cae un 20 % -> lo vende todo; su dinero vuelve a la reserva. No resucita.
  - HIJO    si gana un 20 % -> crea un bot hijo y le da el 25 % de su dinero.
            El hijo hereda la estrategia con alguna mutación (otra media u otro gusto).
            El padre vuelve a empezar a contar desde su nuevo dinero.
Todos los bots comparten la cuenta real de Alpaca: se suman sus posiciones y se envía
solo la diferencia neta por activo.

Uso:  python bot_alpaca.py            -> opera y guarda el estado
      python bot_alpaca.py --prueba   -> solo dice lo que haría (no guarda ni opera)
      python bot_alpaca.py --familia  -> muestra el árbol de bots
"""
import json
import os
import random

import cerebro as C
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

DIR = Path(__file__).parent
LOG = DIR / "registro.log"
ESTADO = DIR / "colonia.json"

PESO = 0.95             # parte del dinero de cada bot que invierte cuando está DENTRO
HERIDO, MUERTO, HIJO = 0.90, 0.80, 1.20
DOTE = 0.25             # parte del dinero del padre que recibe el hijo
MAX_BOTS = 10
CAPITAL = 50_000.0      # 50.000 $ = 100 € reales (escala x500); la otra mitad es para la colonia diaria
ESCALA = CAPITAL / 100  # cuántos $ de la colonia equivalen a 1 € real
DOTE_MIN = 10 * ESCALA  # no tiene hijos si la dote no llega a 10 € reales
COMISION = 0.001        # Trading 212 con ETF UCITS en euros: 0 comisión, 0 cambio divisa, ~0,1 % diferencial (estimado)
ORDEN_MIN = 1 * ESCALA  # no se mueven menos de 1 € real
DECIMALES = 6           # fracciones de acción
MEDIAS = [50, 100, 150, 200, 250]
GUSTOS = ["historia", "fuerza", "seguro"]
FUNDADORES = [(200, "historia"), (150, "fuerza"), (200, "seguro")]


def cargar_env():
    if not (DIR / ".env").exists():  # en GitHub las claves vienen como variables de entorno (Secrets)
        return
    for linea in (DIR / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in linea and not linea.strip().startswith("#"):
            k, v = linea.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def log(msg):
    linea = f"{datetime.now():%Y-%m-%d %H:%M} | {msg}"
    print(linea)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


def nuevo_bot(estado, padre, media, gusto, dinero):
    estado["siguiente_id"] += 1
    bot = {"id": estado["siguiente_id"], "padre": padre, "activo": None, "media": media, "gusto": gusto,
           "efectivo": dinero, "acciones": 0, "referencia": dinero, "inicial": dinero, "estado": "vivo",
           "nacido": f"{datetime.now():%Y-%m-%d}", "hijos": 0}
    estado["bots"].append(bot)
    return bot


def mutar(padre):
    media, gusto = padre["media"], padre.get("gusto", "historia")
    if random.random() < 0.4:
        gusto = random.choice([g for g in GUSTOS if g != gusto])
    if random.random() < 0.6:
        media = random.choice([m for m in MEDIAS if m != media])
    return media, gusto


def cargar_estado():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    estado = {"siguiente_id": 0, "reserva": 0.0, "bots": []}
    for media, gusto in FUNDADORES:
        nuevo_bot(estado, None, media, gusto, round(CAPITAL / len(FUNDADORES), 2))
    return estado


GEMELOS = {"QQQ": "EQQQ (Nasdaq-100 UCITS)", "SPY": "SXR8 (S&P 500 UCITS)", "GLD": "4GLD (Xetra-Gold)"}
REAL_EUR = 100.0        # dinero real que representaría la colonia


def eurusd(estado):
    """Cambio euro-dólar del día: BCE (frankfurter) -> Yahoo -> último guardado."""
    r = None
    try:
        import urllib.request
        url = "https://api.frankfurter.app/latest?from=EUR&to=USD"
        r = float(json.load(urllib.request.urlopen(url, timeout=15))["rates"]["USD"])
    except Exception:
        try:
            import yfinance as yf
            r = float(yf.Ticker("EURUSD=X").history(period="5d")["Close"].dropna().iloc[-1])
        except Exception:
            log("Aviso: no se pudo leer el cambio euro-dólar; uso el último guardado.")
    if r:
        estado["eurusd"] = r
        estado.setdefault("eurusd_inicio", r)  # el cambio del primer día con dato real
    return estado.get("eurusd", 1.08)


def en_eur(usd, estado):
    return usd / estado.get("eurusd", 1.08)


def con_100(usd, estado):
    """Lo que valdría esa parte con 100 € reales (incluye el efecto del cambio euro-dólar)."""
    inicio = estado.get("eurusd_inicio", estado.get("eurusd", 1.08))
    return en_eur(usd, estado) / (CAPITAL / inicio) * REAL_EUR


def valor(bot, precios):
    return bot["efectivo"] + (bot["acciones"] * precios.get(bot["activo"], 0) if bot["acciones"] else 0)


def familia(estado):
    print(f"Cambio: 1 € = {estado.get('eurusd', 1.08):.4f} $ {'' if 'eurusd' in estado else '(aprox., sin dato aún)'} | Reserva: {estado['reserva']:,.2f} $")
    hijos = {}
    for b in estado["bots"]:
        hijos.setdefault(b["padre"], []).append(b)

    def pinta(b, nivel):
        icono = {"vivo": "🟢", "herido": "🟠", "muerto": "💀"}[b["estado"]]
        v = b["efectivo"] + b["acciones"] * estado.get("precios", {}).get(b["activo"], 0)
        print(f"{'   ' * nivel}{icono} Bot {b['id']} | {b['activo'] or 'buscando'} | media {b['media']} gusto {b.get('gusto', 'historia')} | "
              f"{v:,.0f} $ = {en_eur(v, estado):,.0f} € | con 100 €: {con_100(v, estado):.2f} € | "
              f"{b['acciones']:.4f} acc. | nacido {b['nacido']}")
        for h in hijos.get(b["id"], []):
            pinta(h, nivel + 1)
    for raiz in hijos.get(None, []):
        pinta(raiz, 0)


def historia_regla(cierres, n):
    """Máquina del Tiempo para bots de tendencia: cada vez que en los últimos años el precio cruzó por
    encima de su media de n días, ¿cuánto ganó manteniendo hasta cruzar por debajo? (con comisiones)"""
    media = cierres.rolling(n).mean()
    dentro = (cierres > media) & media.notna()
    res, entrada = [], None
    for px, d in zip(cierres.values, dentro.values):
        if d and entrada is None:
            entrada = px
        elif not d and entrada is not None:
            res.append(px / entrada * (1 - 2 * COMISION) - 1)
            entrada = None
    if not res:
        return {"n": 0, "media": 0.0, "ganadores": 0.0}
    return {"n": len(res), "media": sum(res) / len(res), "ganadores": sum(r > 0 for r in res) / len(res)}




def noticias_malas(a, cache):
    """Noticias de la skill: devuelve el titular malo (halt/legal/regulatory/risk) o None."""
    import bot_skill as S  # aquí para no crear un import circular
    if a not in cache:
        nt = S.herramienta("trader-news/news.py", "--tickers", a, "--hours", "24", "--no-sec", "--json", "--dry-run", "--all")
        malas = [i for i in (nt or {}).get("items", []) if S.NOTICIAS_MALAS & set(i.get("tags", []))]
        cache[a] = malas[0] if malas else None
    return cache[a]


def investigar(bot, cierres, precios, hist_cache, noticias_cache, ocupados, prohibidos):
    """El bot recorre todo el universo con su regla y elige dónde entrar (o None)."""
    n, gusto = bot["media"], bot.get("gusto", "historia")
    candidatos = []
    for a, c in cierres.items():
        if a in prohibidos or len(c) < n + 50 or precios[a] <= float(c.tail(n).mean()):
            continue
        if (a, n) not in hist_cache:
            hist_cache[(a, n)] = historia_regla(c, n)
        h = hist_cache[(a, n)]
        if h["n"] < 5 or h["media"] <= 0:
            continue
        fuerza = precios[a] / float(c.iloc[-127]) - 1 if len(c) > 127 else 0.0
        nota = {"historia": h["media"], "fuerza": fuerza, "seguro": h["ganadores"]}[gusto]
        candidatos.append((a in ocupados, -nota, a, h, fuerza))  # primero lo que no tiene un hermano
    candidatos.sort(key=lambda x: (x[0], x[1]))
    log(f"🔎 Bot {bot['id']} (media {n}, gusto {gusto}) investiga {len(cierres)} valores: {len(candidatos)} le sirven"
        + (f"; mejores: {', '.join(c[2] for c in candidatos[:5])}" if candidatos else ""))
    for _, _, a, h, fuerza in candidatos[:5]:
        mala = noticias_malas(a, noticias_cache)
        if mala:
            log(f"📰 Bot {bot['id']} descarta {a}: {mala['title'][:80]} ({', '.join(mala['tags'])})")
            continue
        log(f"🕰️ Bot {bot['id']} elige {a}: {h['n']} entradas pasadas con su regla, media {h['media']:+.1%}, "
            f"{h['ganadores']:.0%} ganadoras (con comisión), 6 meses {fuerza:+.1%} -> ✅ entra")
        return a
    return None


def main(prueba=False):
    import universo as U
    cargar_env()
    key, secret = os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"]
    trading = TradingClient(key, secret, paper=True)  # SIEMPRE paper
    datos = StockHistoricalDataClient(key, secret)

    estado = cargar_estado()
    if "--familia" in sys.argv:
        familia(estado)
        return

    simbolos = list(dict.fromkeys(U.universo() + [b["activo"] for b in estado["bots"] if b.get("activo")]))
    barras = datos.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=simbolos, timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=365 * 6), feed="iex")).df  # 6 años: para la historia de cada regla
    presentes = set(barras.index.get_level_values(0))
    cierres = {a: barras.loc[a]["close"] for a in simbolos if a in presentes}
    precios = {a: float(c.iloc[-1]) for a, c in cierres.items()}
    estado["precios"] = {}
    eurusd(estado)

    # 1) vida, muerte y nacimientos
    for bot in list(estado["bots"]):
        if bot["estado"] == "muerto":
            continue
        v = valor(bot, precios)
        ratio = v / bot["referencia"]
        if ratio <= MUERTO:
            if bot["acciones"]:
                v -= bot["acciones"] * precios[bot["activo"]] * COMISION
            bot["efectivo"], bot["acciones"], bot["estado"], bot["activo"] = 0, 0, "muerto", None
            estado["reserva"] += v
            log(f"💀 Bot {bot['id']} ha MUERTO ({ratio - 1:+.1%}). Su dinero ({v:,.2f}$) va a la reserva.")
            continue
        bot["estado"] = "herido" if ratio <= HERIDO else "vivo"
        vivos = sum(b["estado"] != "muerto" for b in estado["bots"])
        if ratio >= HIJO and vivos < MAX_BOTS and v * DOTE >= DOTE_MIN:
            dote = v * DOTE
            falta = max(0.0, dote - bot["efectivo"])
            if falta > 0 and bot["acciones"]:
                vender = round(min(bot["acciones"], falta * (1 + COMISION) / precios[bot["activo"]]), DECIMALES)
                bot["acciones"] -= vender
                bot["efectivo"] += vender * precios[bot["activo"]] * (1 - COMISION)
            bot["efectivo"] -= dote
            bot["hijos"] += 1
            bot["referencia"] = valor(bot, precios)
            media, gusto = mutar(bot)
            hijo = nuevo_bot(estado, bot["id"], media, gusto, dote)
            log(f"👶 Bot {bot['id']} ({ratio - 1:+.1%}) tiene un HIJO: Bot {hijo['id']} "
                f"(media {media}, gusto {gusto}) con {dote:,.2f}$")

    # 2) cada bot decide: sigue en lo suyo, lo vende, o investiga dónde entrar
    noticias_cache, hist_cache = {}, {}
    prohibidos = U.de_skill()
    for bot in estado["bots"]:
        if bot["estado"] == "muerto":
            continue
        n, a = bot["media"], bot.get("activo")
        if bot["acciones"] > 0 and a in precios and precios[a] <= float(cierres[a].tail(n).mean()):
            bot["efectivo"] += bot["acciones"] * precios[a] * (1 - COMISION)
            log(f"Bot {bot['id']} VENDE {bot['acciones']:.4f} {a} a {precios[a]:.2f}: ha caído bajo su media {n}.")
            bot["acciones"], bot["activo"], a = 0, None, None
        if not bot["acciones"]:
            ocupados = {b["activo"] for b in estado["bots"] if b is not bot and b.get("activo") and b["acciones"] > 0}
            try:
                a = investigar(bot, cierres, precios, hist_cache, noticias_cache, ocupados, prohibidos)
            except Exception as ex:
                log(f"  Bot {bot['id']}: la investigación falló ({ex}); sigue en efectivo.")
                a = None
            if a and C.panico(estado, a, log):
                if C.OBEDECER:
                    a = None
                else:
                    log(f"🧠 Bot {bot['id']} entra en {a} aunque el cerebro diría NO (noticias de pánico).")
        if not a or a not in precios:
            bot["activo"] = None if not bot["acciones"] else bot["activo"]
            log(f"Bot {bot['id']} [{bot['estado']}] media {n} | FUERA, en efectivo | vida {valor(bot, precios):,.0f}$")
            continue
        bot["activo"] = a
        peso = PESO * (0.5 if bot["estado"] == "herido" else 1.0)
        objetivo = round(valor(bot, precios) * peso / precios[a] / (1 + COMISION), DECIMALES)
        cambio = round(objetivo - bot["acciones"], DECIMALES)
        if abs(cambio * precios[a]) >= ORDEN_MIN:
            bot["acciones"] = round(bot["acciones"] + cambio, DECIMALES)
            bot["efectivo"] -= cambio * precios[a] + abs(cambio * precios[a]) * COMISION
        if not bot["acciones"]:
            bot["activo"] = None
            continue
        estado["precios"][a] = precios[a]
        log(f"Bot {bot['id']} [{bot['estado']}] {a} {precios[a]:.2f} media{n} | DENTRO | vida {valor(bot, precios):,.0f}$ "
            f"= {en_eur(valor(bot, precios), estado):,.0f}€ (con 100 €: {con_100(valor(bot, precios), estado):.2f}€) "
            f"| {bot['acciones']:.4f} acc.")

    # 3) enviar a Alpaca solo la diferencia neta (sumando también los bots diarios)
    U.sincronizar(trading, precios, log, prueba=prueba, minimo=ORDEN_MIN, sobrescribir={"colonia.json": estado})

    vivos = [b for b in estado["bots"] if b["estado"] != "muerto"]
    tot = sum(valor(b, precios) for b in vivos) + estado["reserva"]
    log(f"Colonia: {len(vivos)} vivos, {len(estado['bots']) - len(vivos)} muertos, reserva {estado['reserva']:,.0f}$ "
        f"| total {tot:,.0f}$ = {en_eur(tot, estado):,.0f}€ | con 100 € reales: {con_100(tot, estado):.2f}€")
    if not prueba:
        ESTADO.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        import panel
        panel.generar()
    except Exception as e:
        print("Aviso: no se pudo actualizar el panel:", e)


if __name__ == "__main__":
    main(prueba="--prueba" in sys.argv)
