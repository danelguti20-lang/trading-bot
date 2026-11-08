"""Bot que aprende: cada 1 de enero se reentrena con TODO el pasado y opera ese año.
Nunca ve datos del futuro (walk-forward). Solo compra o se queda fuera, sin apalancamiento.
Predice si los próximos 5 días serán positivos; entra si la probabilidad > UMBRAL.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from backtest import ASSETS, COST, rsi, stats, strategies

UMBRAL = 0.55
HORIZONTE = 5
INICIO_TEST = 2019


def features(c):
    f = pd.DataFrame(index=c.index)
    for n in (1, 5, 20, 60):
        f[f"ret{n}"] = c.pct_change(n)
    for n in (20, 50, 200):
        f[f"dist{n}"] = c / c.rolling(n).mean() - 1
    f["rsi2"], f["rsi14"] = rsi(c, 2), rsi(c, 14)
    f["vol20"] = c.pct_change().rolling(20).std()
    f["vol_ratio"] = f["vol20"] / c.pct_change().rolling(100).std()
    f["max_dd60"] = c / c.rolling(60).max() - 1
    return f


def walk_forward(c, make_model):
    X = features(c)
    y = (c.shift(-HORIZONTE) / c - 1 > 0).astype(int)
    pos = pd.Series(0.0, index=c.index)
    for year in range(INICIO_TEST, c.index[-1].year + 1):
        # entrenar solo con datos cuyo resultado ya se conocía antes del 1 de enero
        train = (c.index < f"{year}-01-01") & X.notna().all(axis=1)
        train &= np.arange(len(c)) < (np.searchsorted(c.index, pd.Timestamp(f"{year}-01-01")) - HORIZONTE)
        test = (c.index.year == year) & X.notna().all(axis=1)
        if train.sum() < 500 or test.sum() == 0:
            continue
        m = make_model().fit(X[train], y[train])
        prob = m.predict_proba(X[test])[:, 1]
        pos[test] = (prob > UMBRAL).astype(float)
    return pos


MODELOS = {
    "IA lineal": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=1000)),
    "IA árboles": lambda: HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.05,
                                                         min_samples_leaf=100, random_state=0),
}


def main():
    rows = []
    for name, tk in ASSETS.items():
        c = pd.read_csv(f"{tk}.csv", parse_dates=["Date"], index_col="Date").iloc[:, 0].dropna()
        dy = 365 if tk == "BTC-USD" else 252
        mask = c.index >= f"{INICIO_TEST}-01-01"
        todas = {k: v for k, v in strategies(c).items()}
        for mname, mk in MODELOS.items():
            todas[mname] = walk_forward(c, mk)
        for sname, pos in todas.items():
            rows.append({"Activo": name, "Estrategia": sname, **stats(c[mask], pos[mask], dy)})
    res = pd.DataFrame(rows).round(2)
    res.to_csv("resultados_ml.csv", index=False)
    pd.set_option("display.width", 200)
    print(f"Periodo nunca visto: {INICIO_TEST}-hoy\n")
    print(res.to_string(index=False))


if __name__ == "__main__":
    main()
