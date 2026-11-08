"""Colonia de bots (cuenta PAPER de Alpaca).

La colonia tiene 50.000 $ = 100 € reales x500 (mismas reglas que con 100 € en Trading 212 con ETF UCITS: fracciones de acción y comisión 0,25 %).
Cada bot tiene su propio dinero virtual ("vida") y una estrategia sencilla:
  estar invertido en SU activo si el precio está por encima de SU media de N días.
Reglas de vida:
  - HERIDO  si su dinero cae un 10 % desde su punto de referencia -> invierte la mitad.
  - MUERTO  si cae un 20 % -> lo vende todo; su dinero vuelve a la reserva. No resucita.
  - HIJO    si gana un 20 % -> crea un bot hijo y le da el 25 % de su dinero.
            El hijo hereda la estrategia con alguna mutación (otro activo u otra media).
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
ACTIVOS = ["QQQ", "SPY", "GLD"]
MEDIAS = [100, 150, 200, 250]
FUNDADORES = [("QQQ", 200), ("SPY", 200), ("GLD", 200)]


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


def nuevo_bot(estado, padre, activo, media, dinero):
    estado["siguiente_id"] += 1
    bot = {"id": estado["siguiente_id"], "padre": padre, "activo": activo, "media": media,
           "efectivo": dinero, "acciones": 0, "referencia": dinero, "inicial": dinero, "estado": "vivo",
           "nacido": f"{datetime.now():%Y-%m-%d}", "hijos": 0}
    estado["bots"].append(bot)
    return bot


def mutar(padre):
    activo, media = padre["activo"], padre["media"]
    if random.random() < 0.4:
        activo = random.choice([a for a in ACTIVOS if a != activo])
    if random.random() < 0.6:
        media = random.choice([m for m in MEDIAS if m != media])
    return activo, media


def cargar_estado():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    estado = {"siguiente_id": 0, "reserva": 0.0, "bots": []}
    for activo, media in FUNDADORES:
        nuevo_bot(estado, None, activo, media, round(CAPITAL / len(FUNDADORES), 2))
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
    return bot["efectivo"] + bot["acciones"] * precios[bot["activo"]]


def familia(estado):
    print(f"Cambio: 1 € = {estado.get('eurusd', 1.08):.4f} $ {'' if 'eurusd' in estado else '(aprox., sin dato aún)'} | Reserva: {estado['reserva']:,.2f} $")
    hijos = {}
    for b in estado["bots"]:
        hijos.setdefault(b["padre"], []).append(b)

    def pinta(b, nivel):
        icono = {"vivo": "🟢", "herido": "🟠", "muerto": "💀"}[b["estado"]]
        v = b["efectivo"] + b["acciones"] * estado.get("precios", {}).get(b["activo"], 0)
        print(f"{'   ' * nivel}{icono} Bot {b['id']} | {b['activo']} -> {GEMELOS[b['activo']]} | media {b['media']} | "
              f"{v:,.0f} $ = {en_eur(v, estado):,.0f} € | con 100 €: {con_100(v, estado):.2f} € | "
              f"{b['acciones']:.4f} acc. | nacido {b['nacido']}")
        for h in hijos.get(b["id"], []):
            pinta(h, nivel + 1)
    for raiz in hijos.get(None, []):
        pinta(raiz, 0)


def main(prueba=False):
    cargar_env()
    key, secret = os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"]
    trading = TradingClient(key, secret, paper=True)  # SIEMPRE paper
    datos = StockHistoricalDataClient(key, secret)

    estado = cargar_estado()
    if "--familia" in sys.argv:
        familia(estado)
        return

    barras = datos.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=ACTIVOS, timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=max(MEDIAS) * 2), feed="iex")).df
    cierres = {a: barras.loc[a]["close"] for a in ACTIVOS}
    precios = {a: float(cierres[a].iloc[-1]) for a in ACTIVOS}
    estado["precios"] = precios
    eurusd(estado)

    # 1) vida, muerte y nacimientos
    for bot in list(estado["bots"]):
        if bot["estado"] == "muerto":
            continue
        v = valor(bot, precios)
        ratio = v / bot["referencia"]
        if ratio <= MUERTO:
            v -= bot["acciones"] * precios[bot["activo"]] * COMISION
            bot["efectivo"], bot["acciones"], bot["estado"] = 0, 0, "muerto"
            estado["reserva"] += v
            log(f"💀 Bot {bot['id']} ha MUERTO ({ratio - 1:+.1%}). Su dinero ({v:,.2f}$) va a la reserva.")
            continue
        bot["estado"] = "herido" if ratio <= HERIDO else "vivo"
        vivos = sum(b["estado"] != "muerto" for b in estado["bots"])
        if ratio >= HIJO and vivos < MAX_BOTS and v * DOTE >= DOTE_MIN:
            dote = v * DOTE
            # el padre paga la dote en efectivo; si no tiene suficiente, vende acciones (virtualmente)
            falta = max(0.0, dote - bot["efectivo"])
            if falta > 0:
                vender = round(min(bot["acciones"], falta * (1 + COMISION) / precios[bot["activo"]]), DECIMALES)
                bot["acciones"] -= vender
                bot["efectivo"] += vender * precios[bot["activo"]] * (1 - COMISION)
            bot["efectivo"] -= dote
            bot["hijos"] += 1
            bot["referencia"] = valor(bot, precios)
            activo, media = mutar(bot)
            hijo = nuevo_bot(estado, bot["id"], activo, media, dote)
            log(f"👶 Bot {bot['id']} ({ratio - 1:+.1%}) tiene un HIJO: Bot {hijo['id']} "
                f"({activo} media {media}) con {dote:,.2f}$")

    # 2) cada bot decide su posición
    for bot in estado["bots"]:
        if bot["estado"] == "muerto":
            continue
        a, n = bot["activo"], bot["media"]
        dentro = precios[a] > float(cierres[a].tail(n).mean())
        if dentro and bot["acciones"] == 0 and C.panico(estado, a, log):
            dentro = False  # cerebro: con noticias de pánico no entra (si ya está dentro, sigue)
        peso = PESO * (0.5 if bot["estado"] == "herido" else 1.0)
        objetivo = round(valor(bot, precios) * peso / precios[a] / (1 + COMISION), DECIMALES) if dentro else 0
        cambio = round(objetivo - bot["acciones"], DECIMALES)
        if abs(cambio * precios[a]) >= ORDEN_MIN or (objetivo == 0 and bot["acciones"] > 0):
            bot["acciones"] = round(bot["acciones"] + cambio, DECIMALES)
            bot["efectivo"] -= cambio * precios[a] + abs(cambio * precios[a]) * COMISION
        log(f"Bot {bot['id']} [{bot['estado']}] {a} {precios[a]:.2f} media{n} | "
            f"{'DENTRO' if dentro else 'FUERA'} | vida {valor(bot, precios):,.0f}$ = {en_eur(valor(bot, precios), estado):,.0f}€ "
            f"(con 100 €: {con_100(valor(bot, precios), estado):.2f}€) | ref {bot['referencia']:,.0f}$ | {bot['acciones']:.4f} acc.")

    # 3) sumar todos los bots y enviar solo la diferencia a Alpaca
    #    primero se cancelan órdenes pendientes para no comprar dos veces lo mismo
    pendientes = [o for o in trading.get_orders() if o.symbol in ACTIVOS]
    if pendientes and not prueba:
        for o in pendientes:
            trading.cancel_order_by_id(o.id)
        log(f"Canceladas {len(pendientes)} órdenes pendientes antes de recalcular.")
    for a in ACTIVOS:
        quiero = sum(b["acciones"] for b in estado["bots"] if b["activo"] == a and b["estado"] != "muerto")
        try:
            tengo = float(trading.get_open_position(a).qty)
        except Exception:
            tengo = 0
        dif = round(quiero - tengo, DECIMALES)
        if abs(dif * precios[a]) < ORDEN_MIN and not (quiero == 0 and tengo > 0):
            continue
        lado = OrderSide.BUY if dif > 0 else OrderSide.SELL
        if prueba:
            log(f"[PRUEBA] {lado.value} {abs(dif)} {a}")
            continue
        orden = trading.submit_order(MarketOrderRequest(
            symbol=a, qty=abs(dif), side=lado, time_in_force=TimeInForce.DAY))
        log(f"Orden enviada: {lado.value} {abs(dif)} {a} (id {orden.id})")

    vivos = [b for b in estado["bots"] if b["estado"] != "muerto"]
    log(f"Colonia: {len(vivos)} vivos, {len(estado['bots']) - len(vivos)} muertos, "
        f"reserva {estado['reserva']:,.0f}$ | total {(tot := sum(valor(b, precios) for b in vivos) + estado['reserva']):,.0f}$ "
        f"= {en_eur(tot, estado):,.0f}€ | con 100 € reales: {con_100(tot, estado):.2f}€")
    if not prueba:
        ESTADO.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        import panel
        panel.generar()
    except Exception as e:
        print("Aviso: no se pudo actualizar el panel:", e)


if __name__ == "__main__":
    main(prueba="--prueba" in sys.argv)
