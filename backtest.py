"""Backtest de estrategias sencillas en varios activos (solo compra, sin apalancamiento).
Señal al cierre del día -> se opera al cierre siguiente (sin mirar al futuro).
Coste: 0,1 % por cada cambio de posición (comisión + diferencial).
"""
import sys
import numpy as np
import pandas as pd

ASSETS = {"Oro": "GLD", "S&P 500": "SPY", "Nasdaq 100": "QQQ", "Bitcoin": "BTC-USD"}
COST = 0.001
SPLIT = "2019-01-01"  # antes: periodo de prueba; después: periodo "nunca visto"


def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def strategies(c):
    sma = lambda n: c.rolling(n).mean()
    out = {"Comprar y mantener": pd.Series(1.0, index=c.index)}
    out["Cruce medias 50/200"] = (sma(50) > sma(200)).astype(float)
    out["Precio > media 200"] = (c > sma(200)).astype(float)

    # RSI(2) rebote dentro de tendencia alcista
    r, s200, s5 = rsi(c, 2), sma(200), sma(5)
    pos, inpos = [], False
    for i in range(len(c)):
        if not inpos and r.iloc[i] < 10 and c.iloc[i] > s200.iloc[i]:
            inpos = True
        elif inpos and c.iloc[i] > s5.iloc[i]:
            inpos = False
        pos.append(1.0 if inpos else 0.0)
    out["Rebote RSI(2)"] = pd.Series(pos, index=c.index)

    # Ruptura de máximos de 50 días, salida en mínimos de 20
    hi, lo = c.rolling(50).max().shift(1), c.rolling(20).min().shift(1)
    pos, inpos = [], False
    for i in range(len(c)):
        if not inpos and c.iloc[i] > hi.iloc[i]:
            inpos = True
        elif inpos and c.iloc[i] < lo.iloc[i]:
            inpos = False
        pos.append(1.0 if inpos else 0.0)
    out["Ruptura 50/20"] = pd.Series(pos, index=c.index)
    return out


def stats(c, pos, days_year):
    ret = c.pct_change().fillna(0)
    p = pos.shift(1).fillna(0)  # se opera al día siguiente
    trades = p.diff().abs().fillna(0)
    strat = p * ret - trades * COST
    eq = (1 + strat).cumprod()
    yrs = len(eq) / days_year
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    sharpe = strat.mean() / strat.std() * np.sqrt(days_year) if strat.std() > 0 else 0
    return {"Anual %": cagr * 100, "Peor caída %": dd * 100, "Sharpe": sharpe,
            "Ret/caída": cagr / abs(dd) if dd < 0 else 0,
            "% invertido": p.mean() * 100, "Operaciones": int(trades.sum() / 2)}


def main():
    rows = []
    for name, tk in ASSETS.items():
        c = pd.read_csv(f"{tk}.csv", parse_dates=["Date"], index_col="Date").iloc[:, 0].dropna()
        dy = 365 if tk == "BTC-USD" else 252
        for sname, pos in strategies(c).items():
            for period, mask in [("2010-2018", c.index < SPLIT), ("2019-hoy", c.index >= SPLIT)]:
                st = stats(c[mask], pos[mask], dy)
                rows.append({"Activo": name, "Estrategia": sname, "Periodo": period, **st})
    res = pd.DataFrame(rows).round(2)
    res.to_csv(sys.argv[1] if len(sys.argv) > 1 else "resultados.csv", index=False)
    pd.set_option("display.width", 200)
    print(res.to_string(index=False))


if __name__ == "__main__":
    main()
