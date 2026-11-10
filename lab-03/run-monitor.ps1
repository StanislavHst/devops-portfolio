param(
    [string]$Config = (Join-Path $PSScriptRoot 'config/api-monitor.json')
)

$ErrorActionPreference = 'Stop'
$secretPath = Join-Path $env:LOCALAPPDATA 'DevOpsLab03/discord-webhook.xml'
$previousWebhook = $env:DISCORD_WEBHOOK_URL
try {
    if (-not $env:DISCORD_WEBHOOK_URL) {
        if (-not (Test-Path -LiteralPath $secretPath)) {
            throw 'Set DISCORD_WEBHOOK_URL or configure the local encrypted webhook first.'
        }
        $secureWebhook = Import-Clixml -LiteralPath $secretPath
        $credential = [System.Net.NetworkCredential]::new('', $secureWebhook)
        $env:DISCORD_WEBHOOK_URL = $credential.Password
    }
    & python (Join-Path $PSScriptRoot 'api_monitor.py') `
        --config $Config `
        --output (Join-Path $PSScriptRoot 'reports/api-monitor-results.json') `
        --log-file (Join-Path $PSScriptRoot 'logs/api-monitor.log')
    $monitorExitCode = $LASTEXITCODE
}
finally {
    $env:DISCORD_WEBHOOK_URL = $previousWebhook
}
exit $monitorExitCode
