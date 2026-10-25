# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

# Bounded Docker CLI execution shared by local and production launchers.

function Write-DockerUtilityMessage {
    param([string]$Text, [string]$Color = "White")

    if (Get-Command Write-ColorOutput -ErrorAction SilentlyContinue) {
        Write-ColorOutput $Text $Color
    } else {
        Write-Host $Text -ForegroundColor $Color
    }
}

function ConvertTo-NativeCommandArgument {
    param([AllowEmptyString()][string]$Value)

    if ($null -eq $Value -or $Value.Length -eq 0) {
        return '""'
    }
    if ($Value -notmatch '[\s"]') {
        return $Value
    }

    # Follow the Windows CommandLineToArgvW escaping rules for quoted arguments.
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
            continue
        }
        if ($character -eq '"') {
            if ($backslashes -gt 0) {
                [void]$builder.Append((('\' * ($backslashes * 2)) -join ''))
            }
            [void]$builder.Append('\"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append((('\' * $backslashes) -join ''))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append((('\' * ($backslashes * 2)) -join ''))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Stop-NativeProcessTree {
    param([int]$ProcessId)

    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-NativeProcessTree -ProcessId ([int]$_.ProcessId) }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Invoke-NativeProcessWithTimeout {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [int]$TimeoutSec = 10,
        [string]$ProgressLabel = ""
    )

    $command = Get-Command $FilePath -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) {
        return [pscustomobject]@{
            Started = $false
            TimedOut = $false
            ExitCode = $null
            StdOut = ""
            StdErr = "command not found: $FilePath"
            Succeeded = $false
        }
    }

    $process = $null
    $stdoutTask = $null
    $stderrTask = $null
    $started = $false
    $timedOut = $false
    $exitCode = $null
    $stdout = ""
    $stderr = ""

    try {
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $command.Source
        if ($ArgumentList.Count -gt 0) {
            $startInfo.Arguments = (($ArgumentList | ForEach-Object {
                ConvertTo-NativeCommandArgument -Value ([string]$_)
            }) -join ' ')
        }
        $startInfo.UseShellExecute = $false
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.CreateNoWindow = $true

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        $started = $process.Start()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()

        $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSec))
        $nextProgress = (Get-Date).AddSeconds(10)
        while (-not $process.WaitForExit(500)) {
            $now = Get-Date
            if ($now -ge $deadline) {
                $timedOut = $true
                Stop-NativeProcessTree -ProcessId $process.Id
                [void]$process.WaitForExit(2000)
                break
            }
            if ($ProgressLabel -and $now -ge $nextProgress) {
                $elapsed = [Math]::Round(($now - $deadline.AddSeconds(-1 * [Math]::Max(1, $TimeoutSec))).TotalSeconds)
                Write-DockerUtilityMessage "  [WAIT] $ProgressLabel (${elapsed}s)" "Gray"
                $nextProgress = $now.AddSeconds(10)
            }
        }
        if (-not $timedOut) {
            $process.WaitForExit()
            $exitCode = $process.ExitCode
        }
    } catch {
        $stderr = $_.Exception.Message
    } finally {
        if ($stdoutTask -and $stdoutTask.IsCompleted) {
            try { $stdout = $stdoutTask.Result } catch { }
        }
        if ($stderrTask -and $stderrTask.IsCompleted) {
            try { $capturedError = $stderrTask.Result } catch { $capturedError = "" }
            if ($capturedError) {
                $stderr = $capturedError
            }
        }
        if ($process) {
            $process.Dispose()
        }
    }

    return [pscustomobject]@{
        Started = $started
        TimedOut = $timedOut
        ExitCode = $exitCode
        StdOut = $stdout
        StdErr = $stderr
        Succeeded = ($started -and -not $timedOut -and $exitCode -eq 0)
    }
}

function Invoke-DockerCommand {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutSec = 10,
        [switch]$Quiet,
        [switch]$ShowOutput,
        [string]$ProgressLabel = ""
    )

    $result = Invoke-NativeProcessWithTimeout -FilePath "docker.exe" -ArgumentList $Arguments -TimeoutSec $TimeoutSec -ProgressLabel $ProgressLabel
    $displayCommand = "docker " + ($Arguments -join ' ')

    if (-not $Quiet) {
        if ($result.TimedOut) {
            Write-DockerUtilityMessage "  [TIMEOUT] Docker did not respond within ${TimeoutSec}s: $displayCommand" "Yellow"
        } elseif (-not $result.Started) {
            Write-DockerUtilityMessage "  [WARN] Docker CLI is unavailable: $($result.StdErr)" "Yellow"
        } elseif (-not $result.Succeeded) {
            $detail = ($result.StdErr.Trim() -split "`r?`n" | Select-Object -First 1)
            if ($detail) {
                Write-DockerUtilityMessage "  [WARN] Docker command failed: $detail" "Yellow"
            }
        }
    }

    if ($ShowOutput) {
        @($result.StdOut, $result.StdErr) |
            Where-Object { $_ } |
            ForEach-Object {
                ($_ -split "`r?`n") | Where-Object { $_ } | ForEach-Object { Write-Host "    $_" }
            }
    }
    return $result
}

function Test-DockerAvailable {
    param(
        [int]$TimeoutSec = 6,
        [switch]$Quiet
    )

    foreach ($ctx in @("desktop-linux", "default")) {
        $contextResult = Invoke-DockerCommand -Arguments @("context", "use", $ctx) -TimeoutSec ([Math]::Min(3, $TimeoutSec)) -Quiet
        if ($contextResult.TimedOut) {
            break
        }
        if (-not $contextResult.Succeeded) {
            continue
        }

        $infoResult = Invoke-DockerCommand -Arguments @("info") -TimeoutSec $TimeoutSec -Quiet
        if ($infoResult.Succeeded) {
            if (-not $Quiet) {
                Write-DockerUtilityMessage "  [OK] Docker context: $ctx" "Green"
            }
            return $true
        }
        if ($infoResult.TimedOut) {
            break
        }
    }
    if (-not $Quiet) {
        Write-DockerUtilityMessage "  [WARN] Docker CLI/daemon is unavailable or did not respond within ${TimeoutSec}s" "Yellow"
    }
    return $false
}
