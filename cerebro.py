"""Cerebro de los bots. Ideas sacadas de day-trader-skill (github.com/Jakeschincariol/day-trader-skill, MIT):

  NOTICIAS (News Monitor) : lee los titulares de Yahoo de las últimas 12 h. Si hay 2 o más de pánico
                            (crash, plunge, recession...), los bots NO compran ese día (los que ya están dentro siguen).
  MEMORIA  (Time Machine) : antes de operar, el bot repite su regla en las últimas ~20 sesiones reales.
                            Si de media perdió dinero, hoy no entra.  (lo calcula bot_diario.py)
  STOP     (Exit Planner) : si una compra cae un 1 % desde el precio de entrada, vende.
  RACHA    (Risk Manager) : si un bot pierde 3 operaciones seguidas, descansa el día siguiente.

Si no hay internet para las noticias, el cerebro no bloquea nada (sigue como antes).

MODO OBSERVADOR (OBEDECER = False): los bots operan igual y el cerebro solo apunta lo que habría hecho.
En la colonia diaria se lleva la cuenta de las operaciones "a favor" y "en contra" del cerebro, para ver
con datos si hacerle caso mejora o no. El STOP sí se aplica siempre.
"""
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

PANICO = ["crash", "crashes", "plunge", "plunges", "plummet", "selloff", "sell-off", "sinks", "tumble", "tumbles",
          "rout", "panic", "recession", "collapse", "collapses", "circuit breaker", "bank run", "default", "war"]
SUBYACENTE = {"QQQM": "QQQ", "VOO": "SPY", "IAU": "GLD", "QQQ": "QQQ", "SPY": "SPY", "GLD": "GLD"}
UMBRAL_PANICO = 2       # titulares de pánico en 12 h para no comprar
STOP = 0.01             # stop de pérdidas por operación (colonia diaria)
PERDIDAS_DESCANSO = 3   # operaciones perdedoras seguidas para descansar un día
REFRESCO_MIN = 60       # cada cuánto se vuelven a leer las noticias
OBEDECER = False        # False = modo observador: solo avisa; True = el cerebro bloquea compras


def _leer_titulares(ticker, horas=12):
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (bot-trading-paper)"})
    raiz = ET.fromstring(urllib.request.urlopen(req, timeout=10).read())
    limite = datetime.now(timezone.utc) - timedelta(hours=horas)
    malos = []
    for it in raiz.iter("item"):
        titulo = it.findtext("title") or ""
        try:
            fecha = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            continue
        if fecha < limite:
            continue
        if any(re.search(rf"\b{re.escape(p)}\b", titulo.lower()) for p in PANICO):
            malos.append(titulo)
    return malos


def panico(estado, activo, log=print):
    """True si las noticias del índice de ese fondo dan miedo. Guarda en caché en estado['noticias']."""
    t = SUBYACENTE.get(activo, activo)
    cache = estado.setdefault("noticias", {})
    ahora = datetime.now(timezone.utc)
    c = cache.get(t)
    if c and (ahora - datetime.fromisoformat(c["hora"])) < timedelta(minutes=REFRESCO_MIN):
        return c["malos"] >= UMBRAL_PANICO
    try:
        malos = _leer_titulares(t)
    except Exception:
        return bool(c) and c["malos"] >= UMBRAL_PANICO  # sin red: usa lo último que supo
    cache[t] = {"hora": ahora.isoformat(), "malos": len(malos), "titulares": malos[:3]}
    if len(malos) >= UMBRAL_PANICO:
        log(f"🧠 Noticias de pánico en {t}: " + " | ".join(malos[:2]))
    return len(malos) >= UMBRAL_PANICO


def apuntar(estado, contra, resultado):
    """Suma una operación cerrada al marcador del cerebro."""
    m = estado.setdefault("cerebro", {})
    k = m.setdefault("en_contra" if contra else "a_favor", {"n": 0, "ganadas": 0, "suma": 0.0})
    k["n"] += 1
    k["ganadas"] += resultado > 0
    k["suma"] = round(k["suma"] + resultado, 6)


def marcador(estado):
    m = estado.get("cerebro", {})
    partes = []
    for clave, nombre in [("a_favor", "haciendo caso al cerebro"), ("en_contra", "contra el cerebro")]:
        k = m.get(clave)
        if k and k["n"]:
            partes.append(f"{nombre}: {k['n']} op., {k['ganadas'] / k['n']:.0%} ganadas, media {k['suma'] / k['n']:+.2%}")
    return " | ".join(partes) or "aún sin operaciones"
