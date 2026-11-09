"""COLONIA CRIPTO 24/7 (cuenta PAPER de Alpaca): bots autónomos que operan a cualquier hora, cualquier día.

La bolsa cierra, las criptomonedas no: esta colonia trabaja las 24 horas los 7 días.
50.000 $ = 100 € reales (escala x500), igual que las otras. Comisión 0,25 % por operación (Alpaca cripto).
Mismas reglas de vida: HERIDO -10 % (invierte la mitad) | MUERTO -20 % | HIJO +20 % (le da el 25 %).
Vidas e hijos se revisan una vez al día (al empezar el día UTC).

AUTÓNOMOS: cada 15 min, el bot en efectivo investiga TODAS las monedas de Alpaca (menos las estables),
mira en cuáles salta su señal (velas de 1 hora), repite su regla en esa moneda los últimos 30 días con
comisiones (Time Machine propia) y entra en la que mejor le habría ido (si ganó). Prefiere no repetir
la moneda de un hermano. Stop de pérdidas del 3 % siempre.
Estrategias (genoma, se mutan en los hijos):
  tendencia p : dentro mientras el precio esté sobre su media de p horas.
  ruptura   p : compra si supera el máximo de las últimas p horas; sale si cae bajo el mínimo de p/2 horas.
  rebote    p : compra si el RSI(2) horario baja de p; sale al superar su media de 5 horas.
Uso:  python bot_cripto.py [--prueba] [--familia]
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

import bot_alpaca as B

ESTADO = B.DIR / "colonia_cripto.json"
CAPITAL = 50_000.0
COMISION = 0.0025
STOP = 0.03
PARAMS = {"tendencia": [24, 48, 96], "ruptura": [12, 24, 48], "rebote": [5, 10, 20]}
FUNDADORES = [("tendencia", 48), ("ruptura", 24), ("rebote", 10)]
ESTABLES = {"USDC/USD", "USDT/USD", "USDG/USD", "DAI/USD"}
DIAS_MEMORIA = 30
TOLERANCIA = 0.01  # Alpaca cobra la comisión en la moneda: diferencias < 1 % no se reajustan


def log(msg):
    if "--prueba" in sys.argv:
        print(f"[CRIPTO] {msg}")
    else:
        B.log(f"[CRIPTO] {msg}")


def nuevo_bot(estado, padre, estrategia, p, dinero):
    estado["siguiente_id"] += 1
    bot = {"id": estado["siguiente_id"], "padre": padre, "activo": None, "estrategia": estrategia, "p": p,
           "efectivo": dinero, "acciones": 0.0, "referencia": dinero, "inicial": dinero, "estado": "vivo",
           "nacido": f"{datetime.now():%Y-%m-%d}", "hijos": 0, "entrada": None}
    estado["bots"].append(bot)
    return bot


def mutar(b):
    e, p = b["estrategia"], b["p"]
    if random.random() < 0.4:
        e = random.choice([x for x in PARAMS if x != e])
        p = random.choice(PARAMS[e])
    elif random.random() < 0.7:
        p = random.choice([x for x in PARAMS[e] if x != p])
    return e, p


def cargar_estado():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    estado = {"siguiente_id": 0, "reserva": 0.0, "bots": [], "dia": None}
    for e, p in FUNDADORES:
        nuevo_bot(estado, None, e, p, round(CAPITAL / len(FUNDADORES), 2))
    return estado


def valor(bot, precios):
    return bot["efectivo"] + (bot["acciones"] * precios.get(bot["activo"], 0) if bot["acciones"] else 0)


def rsi(c, n=2):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def senales(v, e, p):
    """Dos series booleanas por vela: entrar / seguir dentro (sin mirar el futuro)."""
    c = v["close"]
    if e == "tendencia":
        s = c > c.rolling(p).mean()
        return s, s
    if e == "ruptura":
        entra = c > v["high"].shift(1).rolling(p).max()
        sigue = c > v["low"].shift(1).rolling(max(2, p // 2)).min()
        return entra, sigue
    r = rsi(c)
    return r < p, c <= c.rolling(5).mean()


def memoria(v, e, p):
    """Time Machine: repite la regla en las últimas DIAS_MEMORIA días con comisiones y stop."""
    v = v.tail(DIAS_MEMORIA * 24 + 100)
    entra, sigue = senales(v, e, p)
    c = v["close"].values
    res, entrada = [], None
    for i in range(100, len(c)):
        if entrada is None:
            if entra.iloc[i]:
                entrada = c[i]
        elif c[i] <= entrada * (1 - STOP) or not sigue.iloc[i]:
            res.append(c[i] / entrada * (1 - 2 * COMISION) - 1)
            entrada = None
    if not res:
        return {"n": 0, "media": 0.0, "total": 0.0, "ganadores": 0.0}
    total = 1.0
    for r in res:
        total *= 1 + r
    return {"n": len(res), "media": sum(res) / len(res), "total": total - 1,
            "ganadores": sum(r > 0 for r in res) / len(res)}


def familia(estado):
    print(f"COLONIA CRIPTO | Reserva: {estado['reserva']:,.2f} $")
    hijos = {}
    for b in estado["bots"]:
        hijos.setdefault(b["padre"], []).append(b)

    def pinta(b, nivel):
        icono = {"vivo": "🟢", "herido": "🟠", "muerto": "💀"}[b["estado"]]
        print(f"{'   ' * nivel}{icono} Bot C{b['id']} | {b['activo'] or 'buscando'} | {b['estrategia']} {b['p']} | "
              f"{valor(b, estado.get('precios', {})):,.0f} $ | nacido {b['nacido']}")
        for h in hijos.get(b["id"], []):
            pinta(h, nivel + 1)
    for raiz in hijos.get(None, []):
        pinta(raiz, 0)


def investigar(estado, bot, velas, precios, ocupados):
    e, p = bot["estrategia"], bot["p"]
    con_senal, buenos = [], []
    for s, v in velas.items():
        entra, _ = senales(v, e, p)
        if bool(entra.iloc[-1]):
            con_senal.append(s)
            clave = f"{s}-{e}-{p}"
            mem = estado.setdefault("memoria", {})
            if mem.get(clave, {}).get("hora") != str(v.index[-1]):
                mem[clave] = {"hora": str(v.index[-1]), **{k: round(x, 5) for k, x in memoria(v, e, p).items()}}
            m = mem[clave]
            if m["n"] >= 3 and m["total"] > 0:
                buenos.append((s in ocupados, -m["total"], s, m))
    buenos.sort(key=lambda x: (x[0], x[1]))
    if con_senal:
        log(f"🔎 Bot C{bot['id']} ({e} {p}) investiga {len(velas)} monedas: señal en {len(con_senal)}, "
            f"{len(buenos)} con su regla ganando en {DIAS_MEMORIA} días"
            + (" (" + ", ".join(f"{x[2]} {x[3]['total']:+.1%}" for x in buenos[:4]) + ")" if buenos else ""))
    if not buenos:
        return None
    _, _, s, m = buenos[0]
    log(f"🕰️ Bot C{bot['id']} elige {s}: en {DIAS_MEMORIA} días su regla hizo {m['n']} operaciones, "
        f"{m['ganadores']:.0%} ganadoras, total {m['total']:+.1%} (con comisión) -> ✅ entra")
    return s


def sincronizar(trading, estado, precios, prueba):
    from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
    from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
    quiero = {}
    for b in estado["bots"]:
        if b["estado"] != "muerto" and b.get("activo") and b["acciones"] > 0:
            quiero[b["activo"]] = quiero.get(b["activo"], 0.0) + b["acciones"]
    tengo = {}
    for pos in trading.get_all_positions():
        if pos.asset_class.value == "crypto":
            tengo[pos.symbol.replace("USD", "/USD") if "/" not in pos.symbol else pos.symbol] = float(pos.qty)
    for o in trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN)):
        if "/" in o.symbol or o.symbol.endswith("USD"):
            s = o.symbol if "/" in o.symbol else o.symbol.replace("USD", "/USD")
            resto = float(o.qty or 0) - float(o.filled_qty or 0)
            tengo[s] = tengo.get(s, 0.0) + (resto if o.side == OrderSide.BUY else -resto)
    for s in sorted(set(quiero) | set(tengo)):
        q, t, px = quiero.get(s, 0.0), tengo.get(s, 0.0), precios.get(s, 0.0)
        if q <= 0 and t > 0:
            if prueba:
                log(f"[PRUEBA] vende todo {s}")
                continue
            try:
                trading.close_position(s.replace("/", ""))
                log(f"Orden enviada: sell todo {s} ({t:.6f})")
            except Exception as ex:
                log(f"  Alpaca no cerró {s}: {ex}")
            continue
        dif = q - t
        if px <= 0 or abs(dif * px) < max(5.0, TOLERANCIA * q * px):
            continue
        lado = OrderSide.BUY if dif > 0 else OrderSide.SELL
        cant = round(abs(dif) if dif > 0 else min(abs(dif), t), 9)
        if prueba:
            log(f"[PRUEBA] {lado.value} {cant:.6f} {s}")
            continue
        try:
            o = trading.submit_order(MarketOrderRequest(symbol=s, qty=cant, side=lado, time_in_force=TimeInForce.GTC))
            log(f"Orden enviada: {lado.value} {cant:.6f} {s} (id {o.id})")
        except Exception as ex:
            log(f"  Alpaca rechazó {lado.value} {cant:.6f} {s}: {ex}")


def main(prueba=False):
    B.cargar_env()
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass
    from alpaca.trading.requests import GetAssetsRequest
    key, secret = os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"]
    trading = TradingClient(key, secret, paper=True)  # SIEMPRE paper
    estado = cargar_estado()
    if "--familia" in sys.argv:
        return familia(estado)

    monedas = [a.symbol for a in trading.get_all_assets(GetAssetsRequest(asset_class=AssetClass.CRYPTO))
               if a.tradable and a.symbol.endswith("/USD") and a.symbol not in ESTABLES]
    ahora = datetime.now(timezone.utc)
    barras = CryptoHistoricalDataClient(key, secret).get_crypto_bars(CryptoBarsRequest(
        symbol_or_symbols=monedas, timeframe=TimeFrame.Hour, start=ahora - timedelta(days=DIAS_MEMORIA + 10))).df
    presentes = set(barras.index.get_level_values(0))
    velas, precios = {}, {}
    for s in monedas:
        if s not in presentes:
            continue
        v = barras.loc[s]
        precios[s] = float(v["close"].iloc[-1])
        v = v[v.index + pd.Timedelta(hours=1) <= pd.Timestamp(ahora)]  # solo velas ya cerradas
        if len(v) > 24 * 20:
            velas[s] = v
    hoy = f"{ahora:%Y-%m-%d}"

    # 1) cada bot decide
    for bot in estado["bots"]:
        if bot["estado"] == "muerto":
            continue
        a, cambio = bot.get("activo"), 0.0
        if bot["acciones"] > 0 and a in precios:
            px = precios[a]
            _, sigue = senales(velas[a], bot["estrategia"], bot["p"]) if a in velas else (None, None)
            parado = px <= bot["entrada"] * (1 - STOP)
            if parado or (sigue is not None and not bool(sigue.iloc[-1])):
                res = px / bot["entrada"] * (1 - 2 * COMISION) - 1
                bot["efectivo"] += bot["acciones"] * px * (1 - COMISION)
                log(f"{'🛑' if parado else '🔚'} Bot C{bot['id']} ({bot['estrategia']} {bot['p']}) VENDE {bot['acciones']:.6f} "
                    f"{a} a {px:.4f} ({'stop' if parado else 'su señal se acabó'}): {res:+.2%} | vida {valor(bot, precios):,.0f}$")
                bot.update(acciones=0.0, activo=None, entrada=None)
        elif not bot["acciones"]:
            bot["activo"] = None
            ocupados = {b["activo"] for b in estado["bots"] if b is not bot and b.get("activo")}
            s = investigar(estado, bot, velas, precios, ocupados)
            if s:
                peso = B.PESO * (0.5 if bot["estado"] == "herido" else 1.0)
                cant = round(bot["efectivo"] * peso / precios[s] / (1 + COMISION), 9)
                if cant * precios[s] >= B.ORDEN_MIN:
                    bot["efectivo"] -= cant * precios[s] * (1 + COMISION)
                    bot.update(acciones=cant, activo=s, entrada=precios[s])
                    log(f"Bot C{bot['id']} ({bot['estrategia']} {bot['p']}) COMPRA {cant:.6f} {s} a {precios[s]:.4f} "
                        f"| vida {valor(bot, precios):,.0f}$")

    # 2) una vez al día: vida, muerte e hijos
    if estado.get("dia") != hoy:
        estado["dia"] = hoy
        for bot in list(estado["bots"]):
            if bot["estado"] == "muerto":
                continue
            v = valor(bot, precios)
            r = v / bot["referencia"]
            if r <= B.MUERTO:
                if bot["acciones"]:
                    v -= bot["acciones"] * precios[bot["activo"]] * COMISION
                bot.update(estado="muerto", efectivo=0, acciones=0.0, activo=None)
                estado["reserva"] += v
                log(f"💀 Bot C{bot['id']} ha MUERTO ({r - 1:+.1%}). {v:,.0f}$ a la reserva.")
                continue
            bot["estado"] = "herido" if r <= B.HERIDO else "vivo"
            vivos = sum(b["estado"] != "muerto" for b in estado["bots"])
            if r >= B.HIJO and vivos < B.MAX_BOTS and v * B.DOTE >= B.DOTE_MIN:
                dote = v * B.DOTE
                falta = max(0.0, dote - bot["efectivo"])
                if falta > 0 and bot["acciones"]:
                    px = precios[bot["activo"]]
                    vender = min(bot["acciones"], falta * (1 + COMISION) / px)
                    bot["acciones"] -= vender
                    bot["efectivo"] += vender * px * (1 - COMISION)
                bot["efectivo"] -= dote
                bot["hijos"] += 1
                bot["referencia"] = valor(bot, precios)
                e, p = mutar(bot)
                h = nuevo_bot(estado, bot["id"], e, p, dote)
                log(f"👶 Bot C{bot['id']} ({r - 1:+.1%}) tiene un HIJO: C{h['id']} ({e} {p}) con {dote:,.0f}$")
        vivos = [b for b in estado["bots"] if b["estado"] != "muerto"]
        tot = sum(valor(b, precios) for b in vivos) + estado["reserva"]
        log(f"Día nuevo: {len(vivos)} vivos | total {tot:,.0f}$ | con 100 € reales: {tot / CAPITAL * 100:.2f}€")

    estado["precios"] = {b["activo"]: precios[b["activo"]] for b in estado["bots"] if b.get("activo") in precios}
    B.eurusd(estado)
    estado["memoria"] = {k: m for k, m in estado.get("memoria", {}).items()
                         if m.get("hora", "") >= str(pd.Timestamp(ahora) - pd.Timedelta(hours=3))}
    sincronizar(trading, estado, precios, prueba)
    if not prueba:
        ESTADO.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        import panel
        panel.generar()
    except Exception as e:
        print("Aviso: no se pudo actualizar el panel:", e)


if __name__ == "__main__":
    main(prueba="--prueba" in sys.argv)
