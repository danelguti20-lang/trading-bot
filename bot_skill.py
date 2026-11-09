"""COLONIA SKILL (cuenta PAPER de Alpaca): day trading siguiendo el método de day-trader-skill
(github.com/Jakeschincariol/day-trader-skill, MIT; herramientas copiadas en skill/).

Cada 15 minutos con la bolsa abierta:
  1. ESCÁNER   (10:00-11:30 Nueva York): busca roturas del rango de apertura de 15 min en todo el universo
               (universo.py: ~140 ETF y acciones + lo más negociado y lo que más se mueve hoy). Autónoma:
               puede ir a cualquiera menos a lo que ya tienen los bots de las otras colonias.
  2. NOTICIAS  : descarta la acción si hay titulares de suspensión, demandas, reguladores o riesgo.
  3. PLAN      : entrada = vela que rompió, stop = mitad del rango de apertura, objetivo = 2R.
  4. MÁQUINA DEL TIEMPO: 10.000 repeticiones + la historia real. Solo compra si, RESTANDO COMISIONES,
                 la simulación da > +0,05R y la historia real > 0R (con al menos 15 casos).
  5. TAMAÑO y RIESGO: pierde como mucho un 0,5 % por trade, máx. 25 % del dinero por acción,
                 máx. 3 trades al día, y nada más si hoy ya pierde un 1,5 %.
  6. ORDEN     : compra con stop y objetivo puestos a la vez (orden "bracket"). A las 15:50 lo vende todo.

50.000 $ virtuales (= 100 € reales, escala x500), igual que las otras colonias. SIEMPRE paper.
Uso:  python bot_skill.py [--prueba] [--estado] [--forzar-hora "2026-11-06 10:15"]
"""
import json
import math
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import bot_alpaca as B

ESTADO = B.DIR / "colonia_skill.json"
SKILL = B.DIR / "skill"
TRABAJO = B.DIR / "skill_trabajo"
CAPITAL = 50_000.0
RIESGO_TRADE = 0.005      # pérdida máxima por trade (del dinero de la colonia)
TOPE_POSICION = 0.25      # máximo del dinero en una sola acción
LIMITE_DIA = 0.015        # pérdida del día que corta las entradas
MAX_TRADES = 3
OBJETIVO_R = 2.0
MIN_SIM_R, MIN_HIST_R, MIN_CASOS = 0.05, 0.0, 15
MAX_CANDIDATAS = 5
VENTANA = ("10:00", "11:30")   # horas de Nueva York en las que busca entradas
CIERRE = "15:50"
NOTICIAS_MALAS = {"halt", "legal", "regulatory", "risk"}
EXCLUIDOS = {"QQQ", "SPY", "GLD", "QQQM", "VOO", "IAU"}   # son de las otras colonias
UNIVERSO = ("AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,AMD,NFLX,ADBE,CRM,ORCL,INTC,QCOM,TXN,MU,AMAT,LRCX,KLAC,"
            "MRVL,PANW,CRWD,SNOW,PLTR,UBER,ABNB,SHOP,PYPL,COIN,MSTR,SMCI,ARM,DELL,IBM,CSCO,NOW,INTU,ANET,DDOG,"
            "JPM,BAC,WFC,GS,MS,C,SCHW,V,MA,AXP,BRK-B,UNH,LLY,JNJ,PFE,MRK,ABBV,AMGN,GILD,MRNA,"
            "XOM,CVX,COP,OXY,SLB,WMT,COST,TGT,HD,LOW,NKE,SBUX,MCD,KO,PEP,PG,DIS,CMCSA,T,VZ,"
            "BA,CAT,DE,GE,HON,LMT,RTX,UPS,FDX,F,GM,RIVN,LCID,NIO,BABA,PDD,SOFI,HOOD,RBLX,DKNG")


def log(msg):
    if "--prueba" in sys.argv:
        print(f"[SKILL] {msg}")  # en prueba no ensucia registro.log
    else:
        B.log(f"[SKILL] {msg}")


def herramienta(script, *args, timeout=120):
    """Ejecuta una herramienta de la skill y devuelve su JSON (o None)."""
    TRABAJO.mkdir(exist_ok=True)
    r = subprocess.run([sys.executable, str(SKILL / script), *args], cwd=TRABAJO,
                       capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    try:
        return json.loads(r.stdout)
    except Exception:
        err = (r.stderr or r.stdout).strip().splitlines()
        log(f"  {script} sin respuesta: {err[-1] if err else 'vacío'}")
        return None


def cargar():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    return {"efectivo": CAPITAL, "abiertos": [], "cerrados": [], "hoy": None, "trades_hoy": 0, "resultado_hoy": 0.0}


def guardar(e):
    ESTADO.write_text(json.dumps(e, indent=2, ensure_ascii=False), encoding="utf-8")


def resumen(e):
    c = e["cerrados"]
    total = e["efectivo"] + sum(t["acciones"] * t["entrada"] for t in e["abiertos"])
    print(f"COLONIA SKILL | dinero {total:,.2f} $ (empezó con {CAPITAL:,.0f}) = {total / CAPITAL * 100:.2f} € con 100 € reales")
    if c:
        g = sum(t["resultado"] > 0 for t in c)
        print(f"  {len(c)} trades cerrados | {g / len(c):.0%} ganadores | total {sum(t['resultado'] for t in c):+,.2f} $")
    for t in e["abiertos"]:
        print(f"  ABIERTO {t['simbolo']} {t['acciones']} acc. entrada {t['entrada']:.2f} stop {t['stop']:.2f} objetivo {t['objetivo']:.2f}")


def evaluar(hit, ahora, dia):
    """Pasos 2-4 para una acción. Devuelve el plan si merece la pena, o None."""
    s = hit["symbol"]
    # 2) noticias
    n = herramienta("trader-news/news.py", "--tickers", s, "--hours", "24", "--no-sec", "--json", "--dry-run", "--all")
    if n:
        malas = [i for i in n.get("items", []) if NOTICIAS_MALAS & set(i.get("tags", []))]
        if malas:
            log(f"📰 {s} descartada por noticias: {malas[0]['title'][:90]} ({', '.join(malas[0]['tags'])})")
            return None
    # 3) plan (como trader-exit: stop en la mitad del rango, redondeado hacia abajo; objetivo 2R)
    entrada = float(hit["price"])
    stop = math.floor((hit["or_high"] + hit["or_low"]) / 2 * 100) / 100
    riesgo = entrada - stop
    if riesgo <= 0.02 or riesgo / entrada < 0.002:
        log(f"  {s}: stop demasiado pegado ({riesgo:.2f} $), no vale.")
        return None
    objetivo = round(entrada + OBJETIVO_R * riesgo, 2)
    # 4) máquina del tiempo
    tm = herramienta("trader-timemachine/timemachine.py", "run", "--symbol", s, "--date", dia,
                     "--entry", f"{entrada:.2f}", "--stop", f"{stop:.2f}", "--target", f"{objetivo:.2f}",
                     "--entry-time", hit["at"], "--until", CIERRE, "--now", ahora, "--json")
    if not tm:
        return None
    coste = 2 * B.COMISION * entrada / riesgo
    sim, hist = tm["mc"]["avg_R"] - coste, tm["history"]["avg_R"] - coste
    casos = tm["history"]["triggered"]
    vale = sim > MIN_SIM_R and hist > MIN_HIST_R and casos >= MIN_CASOS
    log(f"🕰️ {s}: entrada {entrada:.2f} stop {stop:.2f} objetivo {objetivo:.2f} | objetivo antes {tm['mc']['p_target']:.0%}, "
        f"stop antes {tm['mc']['p_stop']:.0%} | con comisión: simulación {sim:+.2f}R, historia {hist:+.2f}R ({casos} casos) "
        f"-> {'✅ COMPRA' if vale else '❌ no'}")
    return {"simbolo": s, "entrada": entrada, "stop": stop, "objetivo": objetivo, "riesgo": riesgo,
            "sim_R": round(sim, 3), "hist_R": round(hist, 3)} if vale else None


def main():
    prueba = "--prueba" in sys.argv
    forzada = sys.argv[sys.argv.index("--forzar-hora") + 1] if "--forzar-hora" in sys.argv else None
    e = cargar()
    if "--estado" in sys.argv:
        return resumen(e)

    B.cargar_env()
    import os
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
    from alpaca.trading.requests import (GetOrdersRequest, MarketOrderRequest, StopLossRequest,
                                         TakeProfitRequest)
    trading = TradingClient(os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"], paper=True)  # SIEMPRE paper

    if forzada:
        ahora = pd.Timestamp(forzada, tz="America/New_York")
    else:
        reloj = trading.get_clock()
        if not reloj.is_open:
            print("Bolsa cerrada: la colonia skill no hace nada.")
            return
        ahora = pd.Timestamp(reloj.timestamp).tz_convert("America/New_York")
    dia, hora = str(ahora.date()), ahora.strftime("%H:%M")
    if e["hoy"] != dia:
        e.update(hoy=dia, trades_hoy=0, resultado_hoy=0.0)

    # A) revisar trades abiertos: ¿saltó el stop o el objetivo?
    for t in list(e["abiertos"]):
        if prueba:
            break
        try:
            orden = trading.get_order_by_id(t["orden"])
        except Exception as ex:
            log(f"  no se pudo leer la orden de {t['simbolo']}: {ex}")
            continue
        if orden.filled_avg_price and not t.get("lleno"):
            t["entrada"], t["lleno"] = float(orden.filled_avg_price), True
        salida = None
        for pata in orden.legs or []:
            if pata.status.value == "filled":
                salida = float(pata.filled_avg_price)
        if orden.status.value in ("canceled", "expired", "rejected") and not orden.filled_avg_price:
            e["efectivo"] += t["acciones"] * t["entrada"] * (1 + B.COMISION)
            e["abiertos"].remove(t)
            log(f"  orden de {t['simbolo']} no se llenó ({orden.status.value}); dinero devuelto.")
        elif salida:
            cerrar(e, t, salida, "stop" if salida <= t["stop"] + 0.01 else "objetivo")

    # B) a las 15:50: vender todo lo de esta colonia
    if hora >= CIERRE:
        for t in list(e["abiertos"]):
            if prueba:
                log(f"[PRUEBA] cerraría {t['simbolo']}")
                continue
            for o in trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[t["simbolo"]])):
                trading.cancel_order_by_id(o.id)
            try:
                pos = trading.get_open_position(t["simbolo"])
                precio = float(pos.current_price)
                trading.close_position(t["simbolo"])
            except Exception:
                precio = t["entrada"]
            cerrar(e, t, precio, "cierre 15:50")
    # C) buscar nuevas entradas
    elif VENTANA[0] <= hora <= VENTANA[1]:
        equity = e["efectivo"] + sum(t["acciones"] * t["entrada"] for t in e["abiertos"])
        if e["trades_hoy"] >= MAX_TRADES:
            log(f"Ya hay {MAX_TRADES} trades hoy: no busca más.")
        elif e["resultado_hoy"] <= -LIMITE_DIA * equity:
            log(f"🛑 Hoy ya pierde {e['resultado_hoy']:,.0f} $: no entra más (límite {LIMITE_DIA:.1%}).")
        else:
            ahora_txt = ahora.strftime("%Y-%m-%d %H:%M")
            import universo as U
            uni = U.universo(os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"], extra=True, log=log)
            ocupados = set(U.libro())
            sc = herramienta("trader-scanner/scan.py", "orb", "--symbols", ",".join(uni), "--now", ahora_txt,
                             "--json", "--no-write", "--quiet", timeout=240)
            hits = [h for h in (sc or {}).get("hits", []) if h["symbol"] not in ocupados
                    and h["symbol"] not in {t["simbolo"] for t in e["abiertos"]}
                    and h["symbol"] not in e.get("vistos_hoy", {}).get(dia, [])]
            # solo roturas recientes (de los últimos 20 min): una entrada tardía es otro trade
            hits = [h for h in hits if (ahora - pd.Timestamp(f"{dia} {h['bar_start']}", tz="America/New_York"))
                    <= timedelta(minutes=25)]
            hits.sort(key=lambda h: h["bar_start"], reverse=True)  # primero las roturas más recientes
            log(f"🔎 Escáner {hora}: {len((sc or {}).get('hits', []))} roturas, {len(hits)} recientes para estudiar.")
            vistos = e.setdefault("vistos_hoy", {})
            vistos = {dia: vistos.get(dia, [])}
            e["vistos_hoy"] = vistos
            for h in hits[:MAX_CANDIDATAS]:
                if e["trades_hoy"] >= MAX_TRADES:
                    break
                vistos[dia].append(h["symbol"])
                try:
                    plan = evaluar(h, ahora_txt, dia)
                except Exception as ex:
                    log(f"  {h['symbol']}: error {ex}")
                    continue
                if not plan:
                    continue
                # tamaño: pierde como mucho RIESGO_TRADE si salta el stop (comisiones incluidas)
                perdida_acc = plan["riesgo"] + 2 * B.COMISION * plan["entrada"]
                acciones = int(min(RIESGO_TRADE * equity / perdida_acc,
                                   TOPE_POSICION * equity / plan["entrada"],
                                   e["efectivo"] / (plan["entrada"] * (1 + B.COMISION))))
                if acciones < 1:
                    log(f"  {plan['simbolo']}: no llega para 1 acción.")
                    continue
                log(f"📏 {plan['simbolo']}: {acciones} acciones ({acciones * plan['entrada']:,.0f} $), "
                    f"pérdida máx. {acciones * perdida_acc:,.0f} $")
                if prueba:
                    log(f"[PRUEBA] compraría {acciones} {plan['simbolo']} con stop {plan['stop']} y objetivo {plan['objetivo']}")
                    e["trades_hoy"] += 1
                    continue
                try:
                    o = trading.submit_order(MarketOrderRequest(
                        symbol=plan["simbolo"], qty=acciones, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
                        order_class=OrderClass.BRACKET, take_profit=TakeProfitRequest(limit_price=plan["objetivo"]),
                        stop_loss=StopLossRequest(stop_price=plan["stop"])))
                except Exception as ex:
                    log(f"  Alpaca rechazó la orden de {plan['simbolo']}: {ex}")
                    continue
                e["efectivo"] -= acciones * plan["entrada"] * (1 + B.COMISION)
                e["abiertos"].append({**plan, "acciones": acciones, "orden": str(o.id), "dia": dia, "hora": hora})
                e["trades_hoy"] += 1
                log(f"🛒 COMPRA {acciones} {plan['simbolo']} | stop {plan['stop']} | objetivo {plan['objetivo']} (orden {o.id})")
    if not prueba:
        guardar(e)


def cerrar(e, t, precio, motivo):
    res = t["acciones"] * (precio - t["entrada"]) - t["acciones"] * (precio + t["entrada"]) * B.COMISION
    e["efectivo"] += t["acciones"] * t["entrada"] * (1 + B.COMISION) + res
    e["resultado_hoy"] += res
    e["abiertos"].remove(t)
    e["cerrados"].append({**t, "salida": precio, "motivo": motivo, "resultado": round(res, 2),
                          "cerrado": datetime.now().strftime("%Y-%m-%d %H:%M")})
    log(f"{'🟢' if res > 0 else '🔴'} {t['simbolo']} cerrado por {motivo} a {precio:.2f}: {res:+,.2f} $")


if __name__ == "__main__":
    main()
