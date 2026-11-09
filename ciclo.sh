#!/usr/bin/env bash
# Ciclo 24/7: nunca se para.
#   - cada 15 min: colonia cripto (siempre) + diaria y skill (solo hacen algo con la bolsa abierta)
#   - tras el cierre (>= 21:35 UTC, L-V): colonia de tendencia, una vez al día
# GitHub corta a las 6 h: antes de eso se relanza solo en una ejecución nueva.
# El horario (cron) de bots.yml es solo de reserva por si la cadena se rompe.
set -u
MAX_SEG=$((340 * 60))
INICIO=$(date +%s)
TENDENCIA=$((21 * 60 + 35))

min_utc() { echo $((10#$(date -u +%H) * 60 + 10#$(date -u +%M))); }

guardar() {
  git config user.name "bot-trading"
  git config user.email "bot-trading@users.noreply.github.com"
  for f in colonia.json colonia_diaria.json colonia_skill.json colonia_cripto.json historial.csv registro.log panel.html tendencia_hecha.txt; do
    [ -f "$f" ] && git add "$f"
  done
  git diff --cached --quiet && return
  git commit -q -m "Estado de los bots $(date '+%Y-%m-%d %H:%M')"
  for i in 1 2 3; do git pull -q --rebase && git push -q && return; sleep 5; done
}

while true; do
  if [ $(( $(date +%s) - INICIO )) -gt "$MAX_SEG" ]; then
    echo "Casi 6 h: relanzo el ciclo en una ejecución nueva."
    gh workflow run bots.yml -f colonia=ciclo
    exit 0
  fi
  echo "=== $(date '+%Y-%m-%d %H:%M') ==="
  python bot_cripto.py || echo "fallo en la colonia cripto"
  if [ "$(date -u +%u)" -le 5 ]; then
    python bot_diario.py || echo "fallo en la colonia diaria"
    python bot_skill.py || echo "fallo en la colonia skill"
    hoy=$(date -u +%F)
    if [ "$(min_utc)" -ge "$TENDENCIA" ] && [ "$(cat tendencia_hecha.txt 2>/dev/null)" != "$hoy" ]; then
      python bot_alpaca.py && echo "$hoy" > tendencia_hecha.txt
    fi
  fi
  python panel.py || true
  guardar
  s=$(( 900 - $(date +%s) % 900 ))
  sleep "$s"
done
