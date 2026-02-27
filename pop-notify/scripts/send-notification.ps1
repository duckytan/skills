# Windows Balloon Notification - PowerShell Version
# Use: Send balloon notification to Windows system tray
# Compatibility: Windows 7/8/10/11
# Dependency: None (uses .NET Framework)

param(
    [string]$Title = "Notification",
    [string]$Message = "This is a notification",
    [ValidateSet("info", "warning", "error", "none")]
    [string]$Icon = "info",
    [int]$Duration = 5000
)

# Load required assemblies
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Map icon types to .NET enum values
$iconMap = @{
    'info' = 'Information'
    'warning' = 'Warning'
    'error' = 'Error'
    'none' = 'Application'
}

$systemIconName = $iconMap[$Icon]
$systemIcon = [System.Drawing.SystemIcons]::$systemIconName

# Create NotifyIcon instance
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = $systemIcon
$notify.Visible = $true
$notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::$($Icon.ToUpper())
$notify.BalloonTipTitle = $Title
$notify.BalloonTipText = $Message

# Show notification
$notify.ShowBalloonTip($Duration)

# Wait for specified duration
Start-Sleep -Milliseconds $Duration

# Cleanup
$notify.Dispose()

Write-Host "SUCCESS: Notification sent" -ForegroundColor Green
exit 0
