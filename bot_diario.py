"""Colonia DIARIA (cuenta PAPER de Alpaca): bots que compran y venden dentro del mismo día.

50.000 $ = 100 € reales (escala x500). Mismas reglas de vida que la colonia de tendencia:
  HERIDO -10 % (invierte la mitad) | MUERTO -20 % (dinero a la reserva) | HIJO +20 % (le da el 25 %).
Usa fondos distintos a la colonia de tendencia (mismos índices) para no mezclar posiciones:
  QQQM = Nasdaq 100, VOO = S&P 500, IAU = oro.

Estrategias (todas cierran antes del final del día):
  ruptura : compra si el precio supera el máximo de los primeros P minutos; sale si cae bajo el mínimo.
  mañana  : a partir de las 12:00 (Nueva York) compra si el precio va un P % por encima de la apertura.
  rebote  : compra si el RSI(2) de velas de 15 min baja de P; vende cuando supera su media de 5 velas.

MÉTODO SKILL (day-trader-skill, como la colonia skill) — este SÍ decide:
  cuando un bot quiere comprar: noticias de la skill (descarta halt/legal/regulatory/risk) -> plan con stop y
  objetivo 2R -> Máquina del Tiempo; solo compra si con comisiones la simulación da > +0,05R y la historia > 0R.
  Tamaño: pierde como mucho un 0,5 % de su dinero si salta el stop. Vende en el stop o en el objetivo.
CEREBRO (cerebro.py, ideas de day-trader-skill): noticias de pánico, memoria de las últimas sesiones,
stop del 1 % y descanso tras 3 pérdidas seguidas.

Ejecutar cada 15 minutos con la bolsa abierta (la tarea programada lo hace; fuera de hora no hace nada).
Uso:  python bot_diario.py [--prueba] [--familia]
"""
import json
import random
import sys
from datetime import datetime, timedelta

import pandas as pd

import bot_alpaca as B
import cerebro as C
import bot_skill as S
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


def memoria(estrategia, p, velas, dias=20):
    """Time Machine: repite la regla en las últimas sesiones completas (sin hoy).
    Devuelve rentabilidad media por día, % de días ganadores y días con operación."""
    fechas = sorted(set(velas.index.date))[:-1][-dias:]
    resultados = []
    for d in fechas:
        hasta = velas[velas.index.date <= d]
        dia = hasta[hasta.index.date == d]
        if len(dia) < 20:
            continue
        bot = {"estrategia": estrategia, "p": p, "acciones": 0, "operado_hoy": False}
        entrada, r, opero = None, 1.0, False
        for i, t in enumerate(dia.index):
            px = float(dia["close"].iloc[i])
            final = i == len(dia) - 1
            parado = entrada is not None and px <= entrada * (1 - C.STOP)
            quiere = False if (final or parado) else senal(bot, hasta[hasta.index <= t], t)
            if quiere and entrada is None:
                entrada, bot["acciones"], bot["operado_hoy"], opero = px, 1, True, True
            elif not quiere and entrada is not None:
                r *= px / entrada * (1 - 2 * B.COMISION)
                entrada, bot["acciones"] = None, 0
        if opero:
            resultados.append(r - 1)
    if not resultados:
        return {"media": 0.0, "ganadores": 0.0, "n": 0}
    return {"media": sum(resultados) / len(resultados),
            "ganadores": sum(x > 0 for x in resultados) / len(resultados), "n": len(resultados)}


def memoria_dice_no(estado, bot, velas, hoy):
    """Calcula la memoria una vez al día por regla; True si la regla viene perdiendo."""
    clave = f"{bot['activo']}-{bot['estrategia']}-{bot['p']}"
    mem = estado.setdefault("memoria", {})
    if mem.get(clave, {}).get("fecha") != hoy:
        m = memoria(bot["estrategia"], bot["p"], velas)
        mem[clave] = {"fecha": hoy, **{k: round(v, 5) for k, v in m.items()}}
        log(f"🧠 Memoria {clave}: {m['n']} días operados, media {m['media']:+.2%}/día, "
            f"{m['ganadores']:.0%} ganadores -> {'NO entra hoy' if m['n'] >= 5 and m['media'] < 0 else 'puede entrar'}")
    m = mem[clave]
    return m["n"] >= 5 and m["media"] < 0


def plan_skill(bot, a, px, velas, ahora, cache):
    """Método de la skill para la compra de un bot. Devuelve el plan o None (y el motivo en el log)."""
    hora_txt = ahora.strftime("%Y-%m-%d %H:%M")
    # noticias (una vez por fondo y ejecución)
    if a not in cache:
        n = S.herramienta("trader-news/news.py", "--tickers", a, "--hours", "24", "--no-sec", "--json", "--dry-run", "--all")
        malas = [i for i in (n or {}).get("items", []) if S.NOTICIAS_MALAS & set(i.get("tags", []))]
        cache[a] = malas[0] if malas else None
    if cache[a]:
        log(f"📰 Bot D{bot['id']} no compra {a}: {cache[a]['title'][:80]} ({', '.join(cache[a]['tags'])})")
        return None
    # plan: en ruptura el stop va a la mitad del rango; en las demás, un 1 % por debajo
    hoy = velas[velas.index.date == ahora.date()]
    stop = px * (1 - C.STOP)
    if bot["estrategia"] == "ruptura":
        rango = hoy[hoy.index < hoy.index[0] + timedelta(minutes=bot["p"])]
        stop = max(stop, (float(rango["high"].max()) + float(rango["low"].min())) / 2)
    stop = int(stop * 100) / 100
    riesgo = px - stop
    if riesgo <= 0.02:
        log(f"  Bot D{bot['id']} {a}: el stop ({stop:.2f}) no queda por debajo del precio ({px:.2f}); no compra.")
        return None
    meta = round(px + S.OBJETIVO_R * riesgo, 2)
    t5 = ahora.floor("5min")
    tm = S.herramienta("trader-timemachine/timemachine.py", "run", "--symbol", a, "--date", str(ahora.date()),
                       "--entry", f"{px:.2f}", "--stop", f"{stop:.2f}", "--target", f"{meta:.2f}",
                       "--entry-time", t5.strftime("%I:%M %p").lstrip("0"), "--until", S.CIERRE,
                       "--now", hora_txt, "--json")
    if not tm:
        return None
    coste = 2 * B.COMISION * px / riesgo
    sim, hist, casos = tm["mc"]["avg_R"] - coste, tm["history"]["avg_R"] - coste, tm["history"]["triggered"]
    vale = sim > S.MIN_SIM_R and hist > S.MIN_HIST_R and casos >= S.MIN_CASOS
    log(f"🕰️ Bot D{bot['id']} {a}: stop {stop:.2f} objetivo {meta:.2f} | con comisión: simulación {sim:+.2f}R, "
        f"historia {hist:+.2f}R ({casos} casos) -> {'✅ COMPRA' if vale else '❌ no'}")
    return {"stop": stop, "meta": meta, "riesgo": riesgo} if vale else None


def main(prueba=False):
    B.cargar_env()
    key, secret = os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"]
    trading = TradingClient(key, secret, paper=True)  # SIEMPRE paper
    datos = StockHistoricalDataClient(key, secret)
    estado = cargar_estado()

    barras = datos.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=ACTIVOS, timeframe=TimeFrame(15, TimeFrameUnit.Minute),
        start=datetime.now() - timedelta(days=35), feed="iex")).df
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
            bot["descansa"] = bot.get("perdidas", 0) >= C.PERDIDAS_DESCANSO
            if bot["descansa"]:
                bot["perdidas"] = 0
                log(f"🧠 Bot D{bot['id']} lleva {C.PERDIDAS_DESCANSO} pérdidas seguidas: hoy DESCANSA.")

    # 1) cada bot decide (al final del día todos venden)
    noticias_cache = {}
    for bot in estado["bots"]:
        if bot["estado"] == "muerto":
            continue
        a, px = bot["activo"], precios[bot["activo"]]
        quiere = False if final else senal(bot, velas[a], ahora)
        # --- salidas del plan de la skill: stop u objetivo ---
        if bot["acciones"] > 0 and bot.get("entrada"):
            stop = bot.get("stop") or bot["entrada"] * (1 - C.STOP)
            if px <= stop or (bot.get("meta") and px >= bot["meta"]):
                if quiere:
                    log(f"{'🛑' if px <= stop else '🎯'} Bot D{bot['id']} {'STOP' if px <= stop else 'OBJETIVO'}: "
                        f"{a} a {px:.2f} (entró a {bot['entrada']:.2f}).")
                quiere = False
                bot["operado_hoy"] = True
        razones = []
        if quiere and bot["acciones"] == 0:
            if bot.get("descansa"):
                razones.append("descanso")
            if memoria_dice_no(estado, bot, velas[a], str(ahora.date())):
                razones.append("memoria")
            if C.panico(estado, a, log):
                razones.append("noticias")
            if razones and C.OBEDECER:
                quiere = False
        plan = None
        if quiere and bot["acciones"] == 0:
            try:
                plan = plan_skill(bot, a, px, velas[a], ahora, noticias_cache)
            except Exception as ex:
                log(f"  Bot D{bot['id']}: la skill falló ({ex}); no compra.")
            if not plan:
                quiere = False
        v = B.valor(bot, precios)
        peso = B.PESO * (0.5 if bot["estado"] == "herido" else 1.0)
        objetivo = round(v * peso / px / (1 + B.COMISION), B.DECIMALES) if quiere else 0.0
        if plan:  # tamaño de la skill: si salta el stop pierde como mucho RIESGO_TRADE de su dinero
            objetivo = min(objetivo, round(S.RIESGO_TRADE * v / (plan["riesgo"] + 2 * B.COMISION * px), B.DECIMALES))
        if quiere and bot["acciones"] > 0:
            objetivo = bot["acciones"]  # ya está dentro: no reajusta
        cambio = round(objetivo - bot["acciones"], B.DECIMALES)
        if cambio != 0 and (abs(cambio * px) >= B.ORDEN_MIN or objetivo == 0):
            bot["acciones"] = round(bot["acciones"] + cambio, B.DECIMALES)
            bot["efectivo"] -= cambio * px + abs(cambio * px) * B.COMISION
            if cambio > 0:
                bot["operado_hoy"] = True
                bot["entrada"] = px
                bot["stop"], bot["meta"] = (plan["stop"], plan["meta"]) if plan else (None, None)
                bot["contra_cerebro"] = bool(razones)
                if razones:
                    log(f"🧠 Bot D{bot['id']} compra aunque el cerebro diría NO ({', '.join(razones)}).")
            elif objetivo == 0 and bot.get("entrada"):
                res = px / bot["entrada"] * (1 - 2 * B.COMISION) - 1
                bot["perdidas"] = bot.get("perdidas", 0) + 1 if res < 0 else 0
                C.apuntar(estado, bot.get("contra_cerebro", False), res)
                bot["entrada"] = bot["stop"] = bot["meta"] = None
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
        log(f"🧠 Marcador del cerebro: {C.marcador(estado)}")

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
