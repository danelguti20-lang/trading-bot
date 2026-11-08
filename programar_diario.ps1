# Programa la colonia DIARIA (cuenta PAPER): cada 15 minutos de 15:00 a 22:15 (hora de España), de lunes a viernes.
# El bot comprueba solo si la bolsa está abierta; fuera de hora no hace nada.
# Para quitarlo:  Unregister-ScheduledTask -TaskName BotTradingDiario -Confirm:$false
$py = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $py -Argument '"C:\Users\Administrator\trading-bot\bot_diario.py"' -WorkingDirectory "C:\Users\Administrator\trading-bot"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 15:00
$rep = New-ScheduledTaskTrigger -Once -At 15:00 -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 7 -Minutes 15)
$trigger.Repetition = $rep.Repetition
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "BotTradingDiario" -Action $action -Trigger $trigger -Settings $settings -Description "Colonia diaria (Alpaca PAPER)" -Force | Select-Object TaskName, State
"Próxima ejecución: " + (Get-ScheduledTask -TaskName BotTradingDiario | Get-ScheduledTaskInfo).NextRunTime
