# Programa el bot (cuenta PAPER) para que se ejecute de lunes a viernes a las 22:30 (tras el cierre de EE. UU.).
# Para quitarlo:  Unregister-ScheduledTask -TaskName BotTradingPaper -Confirm:$false
$py = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $py -Argument '"C:\Users\Administrator\trading-bot\bot_alpaca.py"' -WorkingDirectory "C:\Users\Administrator\trading-bot"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 22:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "BotTradingPaper" -Action $action -Trigger $trigger -Settings $settings -Description "Bot QQQ media 200 (Alpaca PAPER)" -Force | Select-Object TaskName, State
"Próxima ejecución: " + (Get-ScheduledTask -TaskName BotTradingPaper | Get-ScheduledTaskInfo).NextRunTime
