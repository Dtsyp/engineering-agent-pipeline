# Generated entry point. Invoke Python directly and preserve each argument.
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.UseShellExecute = $false
$start.WorkingDirectory = (Get-Location).ProviderPath
$start.FileName = __PYTHON__
$values = @(__ENTRY__) + @($args)
if ($null -ne $start.PSObject.Properties['ArgumentList']) {
    foreach ($value in $values) { $start.ArgumentList.Add([string]$value) }
} else {
    # Windows PowerShell 5.1 uses the Microsoft C runtime quoting convention.
    $encoded = foreach ($value in $values) {
        $quoted = [regex]::Replace([string]$value, '(\\*)"', '$1$1\"')
        '"' + [regex]::Replace($quoted, '(\\+)$', '$1$1') + '"'
    }
    $start.Arguments = $encoded -join ' '
}
$process = [System.Diagnostics.Process]::Start($start)
$process.WaitForExit()
exit $process.ExitCode
