"""Simula la colonia de bots con datos diarios reales (2019-hoy), con las mismas reglas que bot_alpaca.py."""
import random
import sys

import pandas as pd

import bot_alpaca as B

COSTE = B.COMISION
INICIO = "2019-01-01"


def simular(semilla):
    random.seed(semilla)
    px = pd.DataFrame({a: pd.read_csv(f"{a}.csv", parse_dates=["Date"], index_col="Date").iloc[:, 0] for a in B.ACTIVOS}).dropna()
    medias = {(a, n): px[a].rolling(n).mean() for a in B.ACTIVOS for n in B.MEDIAS}
    est = {"siguiente_id": 0, "reserva": 0.0, "bots": []}
    for a, n in B.FUNDADORES:
        B.nuevo_bot(est, None, a, n, B.CAPITAL / len(B.FUNDADORES))["nacido"] = INICIO
    nac, muer = 0, 0
    for fecha in px.index[px.index >= INICIO]:
        precios = px.loc[fecha].to_dict()
        for bot in list(est["bots"]):
            if bot["estado"] == "muerto":
                continue
            v = B.valor(bot, precios)
            r = v / bot["referencia"]
            if r <= B.MUERTO:
                bot.update(estado="muerto", acciones=0, efectivo=0)
                est["reserva"] += v * (1 - COSTE)
                muer += 1
                continue
            bot["estado"] = "herido" if r <= B.HERIDO else "vivo"
            if r >= B.HIJO and v * B.DOTE >= B.DOTE_MIN and sum(b["estado"] != "muerto" for b in est["bots"]) < B.MAX_BOTS:
                dote = v * B.DOTE
                falta = max(0.0, dote - bot["efectivo"])
                if falta:
                    vend = min(bot["acciones"], falta / precios[bot["activo"]])
                    bot["acciones"] -= vend
                    bot["efectivo"] += vend * precios[bot["activo"]]
                bot["efectivo"] -= dote
                bot["referencia"] = B.valor(bot, precios)
                a, n = B.mutar(bot)
                h = B.nuevo_bot(est, bot["id"], a, n, dote)
                h["nacido"] = str(fecha.date())
                nac += 1
        for bot in est["bots"]:
            if bot["estado"] == "muerto":
                continue
            a, n = bot["activo"], bot["media"]
            dentro = precios[a] > medias[(a, n)].loc[fecha]
            peso = B.PESO * (0.5 if bot["estado"] == "herido" else 1)
            obj = B.valor(bot, precios) * peso / precios[a] if dentro else 0
            cambio = obj - bot["acciones"]
            if abs(cambio * precios[a]) >= max(B.ORDEN_MIN, 0.02 * B.valor(bot, precios)) or (obj == 0 and bot["acciones"]):
                bot["efectivo"] -= cambio * precios[a] + abs(cambio * precios[a]) * COSTE
                bot["acciones"] = obj
    precios = px.iloc[-1].to_dict()
    total = sum(B.valor(b, precios) for b in est["bots"] if b["estado"] != "muerto") + est["reserva"]
    años = (px.index[-1] - pd.Timestamp(INICIO)).days / 365.25
    return est, total, nac, muer, años


if __name__ == "__main__":
    res = []
    for s in range(20):
        est, total, nac, muer, años = simular(s)
        res.append({"semilla": s, "final $": round(total, 2), "anual %": round(((total / B.CAPITAL) ** (1 / años) - 1) * 100, 1),
                    "nacidos": nac, "muertos": muer,
                    "vivos": sum(b["estado"] != "muerto" for b in est["bots"])})
    df = pd.DataFrame(res)
    print(df.to_string(index=False))
    print("\nMedia anual %:", df["anual %"].mean().round(1), "| peor:", df["anual %"].min(), "| mejor:", df["anual %"].max())
    est, *_ = simular(0)
    print("\nÁrbol de ejemplo (semilla 0), estado final:")
    B.familia(est)
