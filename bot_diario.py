"""Colonia DIARIA (cuenta PAPER de Alpaca): bots que compran y venden dentro del mismo día.

50.000 $ = 100 € reales (escala x500). Mismas reglas de vida que la colonia de tendencia:
  HERIDO -10 % (invierte la mitad) | MUERTO -20 % (dinero a la reserva) | HIJO +20 % (le da el 25 %).
Usa fondos distintos a la colonia de tendencia (mismos índices) para no mezclar posiciones:
  QQQM = Nasdaq 100, VOO = S&P 500, IAU = oro.

Estrategias (todas cierran antes del final del día):
  ruptura : compra si el precio supera el máximo de los primeros P minutos; sale si cae bajo el mínimo.
  mañana  : a partir de las 12:00 (Nueva York) compra si el precio va un P % por encima de la apertura.
  rebote  : compra si el RSI(2) de velas de 15 min baja de P; vende cuando supera su media de 5 velas.

Ejecutar cada 15 minutos con la bolsa abierta (la tarea programada lo hace; fuera de hora no hace nada).
Uso:  python bot_diario.py [--prueba] [--familia]
"""
import json
import random
import sys
from datetime import datetime, timedelta

import pandas as pd

import bot_alpaca as B
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
import os

ESTADO = B.DIR / "colonia_diaria.json"
ACTIVOS = ["QQQM", "VOO", "IAU"]
GEMELOS = {"QQQM": "EQQQ (Nasdaq-100 UCITS)", "VOO": "SXR8 (S&P 500 UCITS)", "IAU": "4GLD (Xetra-Gold)"}
PARAMS = {"ruptura": [30, 60], "mañana": [0.2, 0.3, 0.5], "rebote": [5, 10, 20]}
FUNDADORES = [("QQQM", "ruptura", 30), ("VOO", "mañana", 0.3), ("IAU", "rebote", 10)]
CIERRE_MIN = 15         # minutos antes del cierre en que se vende todo y se revisan vidas


def log(msg):
    B.log(f"[DIARIO] {msg}")


def nuevo_bot(estado, padre, activo, estrategia, p, dinero):
    estado["siguiente_id"] += 1
    bot = {"id": estado["siguiente_id"], "padre": padre, "activo": activo, "estrategia": estrategia, "p": p,
           "efectivo": dinero, "acciones": 0.0, "referencia": dinero, "inicial": dinero, "estado": "vivo",
           "nacido": f"{datetime.now():%Y-%m-%d}", "hijos": 0, "hoy": None, "operado_hoy": False}
    estado["bots"].append(bot)
    return bot


def mutar(b):
    activo, estr, p = b["activo"], b["estrategia"], b["p"]
    if random.random() < 0.4:
        activo = random.choice([a for a in ACTIVOS if a != activo])
    if random.random() < 0.3:
        estr = random.choice([e for e in PARAMS if e != estr])
        p = random.choice(PARAMS[estr])
    elif random.random() < 0.6 and len(PARAMS[estr]) > 1:
        p = random.choice([x for x in PARAMS[estr] if x != p])
    return activo, estr, p


def cargar_estado():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    estado = {"siguiente_id": 0, "reserva": 0.0, "bots": []}
    for activo, estr, p in FUNDADORES:
        nuevo_bot(estado, None, activo, estr, p, round(B.CAPITAL / len(FUNDADORES), 2))
    return estado


def familia(estado, precios):
    print(f"COLONIA DIARIA | Reserva: {estado['reserva']:,.2f} $")
    hijos = {}
    for b in estado["bots"]:
        hijos.setdefault(b["padre"], []).append(b)

    def pinta(b, nivel):
        icono = {"vivo": "🟢", "herido": "🟠", "muerto": "💀"}[b["estado"]]
        v = b["efectivo"] + b["acciones"] * precios.get(b["activo"], 0)
        print(f"{'   ' * nivel}{icono} Bot D{b['id']} | {b['activo']} -> {GEMELOS[b['activo']]} | {b['estrategia']} {b['p']} | "
              f"{v:,.0f} $ = {B.en_eur(v, estado):,.0f} € | con 100 €: {B.con_100(v, estado):.2f} € | nacido {b['nacido']}")
        for h in hijos.get(b["id"], []):
            pinta(h, nivel + 1)
    for raiz in hijos.get(None, []):
        pinta(raiz, 0)


def rsi(c, n=2):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def senal(bot, velas, ahora):
    """Devuelve True si el bot quiere estar dentro ahora mismo."""
    hoy = velas[velas.index.date == ahora.date()]
    if hoy.empty:
        return False
    precio = float(hoy["close"].iloc[-1])
    dentro = bot["acciones"] > 0
    e, p = bot["estrategia"], bot["p"]
    if e == "ruptura":
        inicio = hoy.index[0]
        rango = hoy[hoy.index < inicio + timedelta(minutes=p)]
        if ahora < inicio + timedelta(minutes=p) or rango.empty:
            return False
        alto, bajo = float(rango["high"].max()), float(rango["low"].min())
        if dentro:
            return precio > bajo
        return precio > alto and not bot["operado_hoy"]
    if e == "mañana":
        if dentro:
            return True
        apertura = float(hoy["open"].iloc[0])
        return ahora.hour >= 12 and precio > apertura * (1 + p / 100) and not bot["operado_hoy"]
    if e == "rebote":
        c = velas["close"]
        if dentro:
            return precio <= float(c.tail(5).mean())
        return float(rsi(c).iloc[-1]) < p
    return False


def main(prueba=False):
    B.cargar_env()
    key, secret = os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"]
    trading = TradingClient(key, secret, paper=True)  # SIEMPRE paper
    datos = StockHistoricalDataClient(key, secret)
    estado = cargar_estado()

    barras = datos.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=ACTIVOS, timeframe=TimeFrame(15, TimeFrameUnit.Minute),
        start=datetime.now() - timedelta(days=7), feed="iex")).df
    velas = {}
    for a in ACTIVOS:
        v = barras.loc[a].copy()
        v.index = v.index.tz_convert("America/New_York")
        velas[a] = v.between_time("09:30", "15:59")
    precios = {a: float(velas[a]["close"].iloc[-1]) for a in ACTIVOS}
    estado["precios"] = precios

    if "--familia" in sys.argv:
        familia(estado, precios)
        return

    reloj = trading.get_clock()
    if not reloj.is_open and not prueba:
        print("Bolsa cerrada: la colonia diaria no hace nada.")
        return
    ahora = pd.Timestamp(reloj.timestamp).tz_convert("America/New_York")
    final = (pd.Timestamp(reloj.next_close).tz_convert("America/New_York") - ahora) <= timedelta(minutes=CIERRE_MIN)
    B.eurusd(estado)

    for bot in estado["bots"]:
        if bot["hoy"] != str(ahora.date()):
            bot["hoy"], bot["operado_hoy"] = str(ahora.date()), False

    # 1) cada bot decide (al final del día todos venden)
    for bot in estado["bots"]:
        if bot["estado"] == "muerto":
            continue
        a, px = bot["activo"], precios[bot["activo"]]
        quiere = False if final else senal(bot, velas[a], ahora)
        v = B.valor(bot, precios)
        peso = B.PESO * (0.5 if bot["estado"] == "herido" else 1.0)
        objetivo = round(v * peso / px / (1 + B.COMISION), B.DECIMALES) if quiere else 0.0
        if quiere and bot["acciones"] > 0:
            objetivo = bot["acciones"]  # ya está dentro: no reajusta
        cambio = round(objetivo - bot["acciones"], B.DECIMALES)
        if cambio != 0 and (abs(cambio * px) >= B.ORDEN_MIN or objetivo == 0):
            bot["acciones"] = round(bot["acciones"] + cambio, B.DECIMALES)
            bot["efectivo"] -= cambio * px + abs(cambio * px) * B.COMISION
            if cambio > 0:
                bot["operado_hoy"] = True
            log(f"Bot D{bot['id']} ({bot['estrategia']} {bot['p']}) {'COMPRA' if cambio > 0 else 'VENDE'} "
                f"{abs(cambio):.4f} {a} a {px:.2f} | vida {B.valor(bot, precios):,.0f}$")

    # 2) al final del día: vida, muerte e hijos (todos están ya en efectivo)
    if final:
        for bot in list(estado["bots"]):
            if bot["estado"] == "muerto":
                continue
            v = B.valor(bot, precios)
            r = v / bot["referencia"]
            if r <= B.MUERTO:
                bot.update(estado="muerto", efectivo=0, acciones=0)
                estado["reserva"] += v
                log(f"💀 Bot D{bot['id']} ha MUERTO ({r - 1:+.1%}). {v:,.0f}$ a la reserva.")
                continue
            bot["estado"] = "herido" if r <= B.HERIDO else "vivo"
            vivos = sum(b["estado"] != "muerto" for b in estado["bots"])
            if r >= B.HIJO and vivos < B.MAX_BOTS and v * B.DOTE >= B.DOTE_MIN:
                dote = v * B.DOTE
                bot["efectivo"] -= dote
                bot["hijos"] += 1
                bot["referencia"] = B.valor(bot, precios)
                a, e, p = mutar(bot)
                h = nuevo_bot(estado, bot["id"], a, e, p, dote)
                log(f"👶 Bot D{bot['id']} ({r - 1:+.1%}) tiene un HIJO: D{h['id']} ({a} {e} {p}) con {dote:,.0f}$")
        vivos = [b for b in estado["bots"] if b["estado"] != "muerto"]
        tot = sum(B.valor(b, precios) for b in vivos) + estado["reserva"]
        log(f"Fin del día: {len(vivos)} vivos | total {tot:,.0f}$ = {B.en_eur(tot, estado):,.0f}€ "
            f"| con 100 € reales: {B.con_100(tot, estado):.2f}€")

    # 3) enviar a Alpaca solo la diferencia neta por fondo
    for a in ACTIVOS:
        quiero = round(sum(b["acciones"] for b in estado["bots"] if b["activo"] == a and b["estado"] != "muerto"), B.DECIMALES)
        try:
            tengo = float(trading.get_open_position(a).qty)
        except Exception:
            tengo = 0.0
        dif = round(quiero - tengo, B.DECIMALES)
        if abs(dif * precios[a]) < 1 and not (quiero == 0 and tengo > 0):
            continue
        lado = OrderSide.BUY if dif > 0 else OrderSide.SELL
        if prueba:
            log(f"[PRUEBA] {lado.value} {abs(dif):.4f} {a}")
            continue
        o = trading.submit_order(MarketOrderRequest(symbol=a, qty=abs(dif), side=lado, time_in_force=TimeInForce.DAY))
        log(f"Orden enviada: {lado.value} {abs(dif):.4f} {a} (id {o.id})")

    if not prueba:
        ESTADO.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        import panel
        panel.generar()
    except Exception as e:
        print("Aviso: no se pudo actualizar el panel:", e)


if __name__ == "__main__":
    main(prueba="--prueba" in sys.argv)
