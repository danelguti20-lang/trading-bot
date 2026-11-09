#!/usr/bin/env bash
# Un solo arranque al día hace todo el trabajo: diaria + skill cada 15 min mientras
# hay bolsa y, tras el cierre, la colonia de tendencia una vez.
# (El horario de GitHub "cada 15 min" se salta ejecuciones; esto no depende de él.)
set -u
FIN=$((21 * 60 + 35))        # 21:35 UTC: después corre la tendencia y se acaba el día
MAX_SEG=$((340 * 60))        # GitHub corta a las 6 h: salimos antes y relanzamos
INICIO=$(date +%s)

min_utc() { echo $((10#$(date -u +%H) * 60 + 10#$(date -u +%M))); }

guardar() {
  git config user.name "bot-trading"
  git config user.email "bot-trading@users.noreply.github.com"
  for f in colonia.json colonia_diaria.json colonia_skill.json historial.csv registro.log panel.html tendencia_hecha.txt; do
    [ -f "$f" ] && git add "$f"
  done
  git diff --cached --quiet && return
  git commit -q -m "Estado de los bots $(date '+%Y-%m-%d %H:%M')"
  for i in 1 2 3; do git pull -q --rebase && git push -q && return; sleep 5; done
}

hoy=$(date -u +%F)
while [ "$(min_utc)" -lt "$FIN" ]; do
  if [ $(( $(date +%s) - INICIO )) -gt "$MAX_SEG" ]; then
    echo "Casi 6 h: relanzo el ciclo en una ejecución nueva."
    gh workflow run bots.yml -f colonia=ciclo
    exit 0
  fi
  echo "=== $(date '+%H:%M') ==="
  python bot_diario.py || echo "fallo en la colonia diaria"
  python bot_skill.py || echo "fallo en la colonia skill"
  python panel.py || true
  guardar
  # dormir hasta el próximo cuarto de hora
  s=$(( 900 - $(date +%s) % 900 ))
  [ "$(min_utc)" -lt "$FIN" ] && sleep "$s"
done

# Tras el cierre: colonia de tendencia, una sola vez al día
if [ "$(cat tendencia_hecha.txt 2>/dev/null)" != "$hoy" ] && [ "$(date -u +%u)" -le 5 ]; then
  python bot_alpaca.py && echo "$hoy" > tendencia_hecha.txt
  python panel.py || true
  guardar
fi
