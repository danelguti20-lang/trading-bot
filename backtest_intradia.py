"""Backtest intradía (velas de 1 hora y 15 minutos) con datos de Alpaca.
Solo compra, sin apalancamiento. Se opera en la vela siguiente a la señal.
Prueba 2 niveles de coste por operación (0,02 % y 0,05 %).
Split: entrenamiento/visto <= 2022, nunca visto >= 2023.
"""
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from backtest import rsi

DIR = Path(__file__).parent
SIMBOLOS = ["QQQ", "SPY", "GLD"]
MARCOS = {"1h": TimeFrame(1, TimeFrameUnit.Hour), "15m": TimeFrame(15, TimeFrameUnit.Minute)}
SPLIT = "2023-01-01"
COSTES = [0.0002, 0.0005]


def cliente():
    for l in (DIR / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in l:
            k, v = l.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    return StockHistoricalDataClient(os.environ["ALPACA_KEY"], os.environ["ALPACA_SECRET"])


def datos(cli, sym, marco):
    f = DIR / f"{sym}_{marco}.csv"
    if f.exists():
        return pd.read_csv(f, index_col=0, parse_dates=True)
    df = cli.get_stock_bars(StockBarsRequest(symbol_or_symbols=sym, timeframe=MARCOS[marco],
                                             start=datetime(2016, 1, 1), feed="iex")).df.droplevel(0)
    df.index = df.index.tz_convert("America/New_York")
    df = df.between_time("09:30", "15:59")  # solo horario normal de bolsa
    df.to_csv(f)
    return df


def maquina(n, entra, sale):
    pos, dentro = np.zeros(n), False
    for i in range(n):
        if not dentro and entra[i]:
            dentro = True
        elif dentro and sale[i]:
            dentro = False
        pos[i] = dentro
    return pos


def estrategias(df):
    c = df["close"]
    fecha = df.index.date
    out = {}
    s20, s50, s200 = (c.rolling(n).mean() for n in (20, 50, 200))
    out["Cruce medias 20/50"] = (s20 > s50).astype(float).values
    out["Precio > media 200 velas"] = (c > s200).astype(float).values

    r = rsi(c, 2)
    out["Rebote RSI(2)"] = maquina(len(c), ((r < 10) & (c > s200)).values, (c > c.rolling(5).mean()).values)

    # Ruptura del rango de la primera hora; se cierra siempre al final del día
    g = df.groupby(fecha)
    t = df.index.time
    primera = (t < pd.Timestamp("10:30").time())
    hi_rango = df["high"].where(primera).groupby(fecha).transform("max")
    ultima = pd.Series(fecha, index=df.index).ne(pd.Series(fecha, index=df.index).shift(-1)).values
    entra = (~primera) & (c > hi_rango).values & (c > s200).values
    out["Ruptura 1ª hora (cierra al día)"] = maquina(len(c), entra, ultima)

    # Momento del día: comprar si la mañana es alcista, cerrar al final
    apertura = g["open"].transform("first")
    entra = (t >= pd.Timestamp("12:00").time()) & (c > apertura * 1.003).values
    out["Mañana fuerte (cierra al día)"] = maquina(len(c), entra, ultima)
    return out


def stats(c, pos, coste, velas_año):
    ret = c.pct_change().fillna(0).values
    p = np.concatenate([[0], pos[:-1]])
    ops = np.abs(np.diff(np.concatenate([[0], p])))
    r = p * ret - ops * coste
    eq = np.cumprod(1 + r)
    años = len(r) / velas_año
    cagr = eq[-1] ** (1 / años) - 1
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    sharpe = r.mean() / r.std() * np.sqrt(velas_año) if r.std() > 0 else 0
    return {"Anual %": cagr * 100, "Peor caída %": dd * 100, "Sharpe": sharpe,
            "% invertido": p.mean() * 100, "Ops/año": ops.sum() / 2 / años}


def main():
    cli = cliente()
    filas = []
    for marco in MARCOS:
        for sym in SIMBOLOS:
            df = datos(cli, sym, marco)
            c = df["close"]
            vpa = len(c) / ((c.index[-1] - c.index[0]).days / 365.25)
            todas = {"Comprar y mantener": np.ones(len(c)), **estrategias(df)}
            for nombre, pos in todas.items():
                for periodo, m in [("hasta 2022", c.index < pd.Timestamp(SPLIT, tz=c.index.tz)),
                                   ("2023-hoy", c.index >= pd.Timestamp(SPLIT, tz=c.index.tz))]:
                    for coste in COSTES:
                        filas.append({"Marco": marco, "Activo": sym, "Estrategia": nombre, "Periodo": periodo,
                                      "Coste %": coste * 100, **stats(c[m], pos[m], coste, vpa)})
            print(f"{marco} {sym}: {len(c)} velas desde {c.index[0].date()}")
    res = pd.DataFrame(filas).round(2)
    res.to_csv(DIR / "resultados_intradia.csv", index=False)


if __name__ == "__main__":
    main()
