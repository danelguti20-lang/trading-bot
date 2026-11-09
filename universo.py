"""DÓNDE BUSCAN LOS BOTS (autónomos) y LIBRO COMÚN de posiciones.

Los bots ya no tienen un activo fijo: cada uno investiga todo el universo con SU regla y elige él mismo.
  universo = ~40 ETF (índices, sectores, países, oro, bonos...) + ~100 acciones grandes
             + lo más negociado y lo que más se mueve HOY (escáner de Alpaca, precio >= 5 $).
Como todas las colonias comparten una sola cuenta de Alpaca, aquí se suma lo que quieren los bots de
tendencia y los diarios para cada acción y se envía solo la diferencia (contando las órdenes pendientes).
Las acciones que tiene abiertas la colonia skill (con su orden bracket) no las toca nadie más.
"""
import json
from pathlib import Path

DIR = Path(__file__).parent

ETF = ("SPY,QQQ,DIA,IWM,MDY,VTI,RSP,"                       # EE. UU.
       "XLK,XLF,XLE,XLV,XLI,XLY,XLP,XLU,XLB,XLRE,XLC,SMH,XBI,KRE,ITB,"   # sectores
       "EFA,EEM,VGK,EWG,EWJ,EWU,EWZ,INDA,FXI,KWEB,"          # resto del mundo
       "GLD,SLV,GDX,USO,TLT,IEF,HYG,IBIT").split(",")          # oro, plata, petróleo, bonos, bitcoin
ACCIONES = ("AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,AMD,NFLX,ADBE,CRM,ORCL,INTC,QCOM,TXN,MU,AMAT,LRCX,KLAC,"
            "MRVL,PANW,CRWD,SNOW,PLTR,UBER,ABNB,SHOP,PYPL,COIN,MSTR,SMCI,ARM,DELL,IBM,CSCO,NOW,INTU,ANET,DDOG,"
            "JPM,BAC,WFC,GS,MS,C,SCHW,V,MA,AXP,UNH,LLY,JNJ,PFE,MRK,ABBV,AMGN,GILD,MRNA,"
            "XOM,CVX,COP,OXY,SLB,WMT,COST,TGT,HD,LOW,NKE,SBUX,MCD,KO,PEP,PG,DIS,CMCSA,T,VZ,"
            "BA,CAT,DE,GE,HON,LMT,RTX,UPS,FDX,F,GM,RIVN,LCID,NIO,BABA,PDD,SOFI,HOOD,RBLX,DKNG").split(",")
BASE = ETF + ACCIONES


def moviendose(key, secret, log=print):
    """Lo más negociado y lo que más sube/baja hoy según Alpaca (si falla, lista vacía)."""
    try:
        from alpaca.data.historical.screener import ScreenerClient
        from alpaca.data.requests import MarketMoversRequest, MostActivesRequest
        sc = ScreenerClient(key, secret)
        simbolos = [a.symbol for a in sc.get_most_actives(MostActivesRequest(top=25)).most_actives]
        mov = sc.get_market_movers(MarketMoversRequest(top=15))
        simbolos += [m.symbol for m in list(mov.gainers) + list(mov.losers) if m.price >= 5]
        return [s for s in dict.fromkeys(simbolos) if s.isalpha() and len(s) <= 5]
    except Exception as ex:
        log(f"  escáner de Alpaca sin respuesta ({ex}); solo la lista base.")
        return []


def universo(key=None, secret=None, extra=False, log=print):
    sim = list(BASE)
    if extra and key:
        sim += moviendose(key, secret, log)
    return list(dict.fromkeys(sim))


def _leer(nombre):
    p = DIR / nombre
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:
        return None


def de_skill():
    """Acciones que tiene abiertas la colonia skill: nadie más las toca."""
    e = _leer("colonia_skill.json") or {}
    return {t["simbolo"] for t in e.get("abiertos", [])}


def libro(sobrescribir=None):
    """Acciones que quieren tener, por símbolo, los bots de tendencia + diarios.
    sobrescribir = {"colonia.json": estado_en_memoria} para usar el estado que aún no se ha guardado."""
    total = {}
    for f in ("colonia.json", "colonia_diaria.json"):
        e = (sobrescribir or {}).get(f) or _leer(f) or {}
        for b in e.get("bots", []):
            if b["estado"] != "muerto" and b.get("activo") and b["acciones"] > 0:
                total[b["activo"]] = total.get(b["activo"], 0.0) + b["acciones"]
    return total


def sincronizar(trading, precios, log, prueba=False, minimo=1.0, sobrescribir=None):
    """Envía a Alpaca la diferencia entre lo que quieren los bots y lo que hay (posiciones + órdenes pendientes)."""
    from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
    from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
    quiero = libro(sobrescribir)
    fuera = de_skill()
    tengo = {p.symbol: float(p.qty) for p in trading.get_all_positions() if p.asset_class.value != "crypto"}
    pendientes = {}
    for o in trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=False)):
        if o.order_class and o.order_class.value != "simple":
            continue  # brackets = colonia skill
        if "/" in o.symbol:
            continue  # cripto: es de bot_cripto.py
        pendientes.setdefault(o.symbol, []).append(o)
        resto = float(o.qty or 0) - float(o.filled_qty or 0)
        tengo[o.symbol] = tengo.get(o.symbol, 0.0) + (resto if o.side == OrderSide.BUY else -resto)
    for s in sorted(set(quiero) | set(tengo)):
        if s in fuera:
            continue
        q, t = round(quiero.get(s, 0.0), 6), round(tengo.get(s, 0.0), 6)
        dif = round(q - t, 6)
        if dif and any((o.side == OrderSide.BUY) != (dif > 0) for o in pendientes.get(s, [])):
            # hay una orden pendiente en sentido contrario: se cancela en vez de operar contra ella
            for o in pendientes[s]:
                resto = float(o.qty or 0) - float(o.filled_qty or 0)
                t -= resto if o.side == OrderSide.BUY else -resto
                if not prueba:
                    try:
                        trading.cancel_order_by_id(o.id)
                        log(f"Cancelada orden pendiente {o.side.value} {resto:.4f} {s} (ya no la quiere ningún bot).")
                    except Exception as ex:
                        log(f"  no se pudo cancelar la orden de {s}: {ex}")
            t = round(t, 6)
            dif = round(q - t, 6)
        px = precios.get(s)
        if px is None:
            try:
                px = float(trading.get_open_position(s).current_price)
            except Exception:
                px = 0.0
        if dif == 0 or (abs(dif * px) < minimo and not (q == 0 and t > 0)) or (dif < 0 and t <= 0):
            continue
        try:
            if not trading.get_asset(s).fractionable:
                dif = float(int(dif))  # sin fracciones: acciones enteras
                if dif == 0:
                    continue
        except Exception:
            pass
        lado = OrderSide.BUY if dif > 0 else OrderSide.SELL
        if prueba:
            log(f"[PRUEBA] {lado.value} {abs(dif):.4f} {s}")
            continue
        try:
            o = trading.submit_order(MarketOrderRequest(symbol=s, qty=abs(dif), side=lado, time_in_force=TimeInForce.DAY))
            log(f"Orden enviada: {lado.value} {abs(dif):.4f} {s} (id {o.id})")
        except Exception as ex:
            log(f"  Alpaca rechazó {lado.value} {abs(dif):.4f} {s}: {ex}")
