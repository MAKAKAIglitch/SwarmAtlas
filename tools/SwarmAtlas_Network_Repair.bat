@echo off
setlocal EnableExtensions
chcp 65001 >nul
title SwarmAtlas - Network Repair

set "SWARM_BAT_PATH=%~f0"
fltmc >nul 2>&1
if errorlevel 1 (
    echo.
    echo SwarmAtlas Network Repair needs Administrator privileges.
    echo Windows will ask for confirmation through UAC.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:ComSpec -ArgumentList ('/c ""' + $env:SWARM_BAT_PATH + '""') -Verb RunAs -Wait"
    exit /b
)

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "SWARM_STAMP=%%I"
set "LOG_DIR=%ProgramData%\SwarmAtlas\Logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
set "LOG_FILE=%LOG_DIR%\NetworkRepair_%SWARM_STAMP%.log"

echo.
echo ============================================================
echo             SWARMATLAS - NETWORK REPAIR
echo ============================================================
echo.
echo This tool changes Windows network configuration.
echo The selected adapter will be temporarily disconnected.
echo.
echo Available network adapters:
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Sort-Object ifIndex | Format-Table -AutoSize ifIndex,Name,Status,InterfaceDescription,LinkSpeed"
echo.
set /p "SWARM_IFINDEX=Enter the Interface Index to repair: "
if not defined SWARM_IFINDEX goto :invalid_adapter

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $i=0; if(-not [int]::TryParse($env:SWARM_IFINDEX,[ref]$i)){exit 2}; $a=Get-NetAdapter -InterfaceIndex $i -ErrorAction Stop; if($null -eq $a){exit 3}; Write-Output ('Selected: ' + $a.Name + ' | ' + $a.InterfaceDescription + ' | Status: ' + $a.Status) } catch { Write-Error $_; exit 3 }" 
if errorlevel 1 goto :invalid_adapter

echo.
echo Choose repair mode:
echo.
echo   [1] Standard adapter repair
echo       - Flush DNS resolver cache
echo       - Restart only the selected network adapter
echo       - Renew DHCP lease if DHCP is enabled
echo       - Check adapter status afterwards
echo.
echo   [2] Standard repair + Winsock reset
echo       - Everything in mode 1
echo       - Reset the Windows Winsock catalog (system-wide)
echo       - A Windows restart may be required
echo.
echo   [Q] Cancel
echo.
set /p "MODE=Select 1, 2 or Q: "
if /i "%MODE%"=="Q" goto :cancelled
if not "%MODE%"=="1" if not "%MODE%"=="2" goto :invalid_mode

echo.
echo ============================================================
echo ACTION REVIEW
echo ============================================================
echo Adapter Interface Index: validated by Windows
if "%MODE%"=="1" (
    echo Mode: Standard adapter repair
    echo  1. Flush DNS cache.
    echo  2. Restart the selected adapter.
    echo  3. Renew DHCP only if enabled for that adapter.
    echo  4. Read back adapter status.
) else (
    echo Mode: Standard repair + Winsock reset
    echo  1. Flush DNS cache.
    echo  2. Restart the selected adapter.
    echo  3. Renew DHCP only if enabled for that adapter.
    echo  4. Reset Winsock for Windows (all applications).
    echo  5. Read back adapter status.
    echo.
    echo WARNING: Winsock reset affects the whole Windows system.
    echo Some VPNs, security tools or network applications may need repair
    echo or a restart after the reset.
)
echo.
echo The adapter may briefly lose connectivity.
echo If you are connected remotely through this adapter, the session may drop.
echo A static IP configuration is not intentionally removed by this script.
echo.
set /p "CONFIRM=Type YES to begin the selected repair: "
if /i not "%CONFIRM%"=="YES" goto :cancelled

echo.
echo Starting repair. Log: "%LOG_FILE%"
echo SwarmAtlas Network Repair started %DATE% %TIME% > "%LOG_FILE%"
echo Interface Index: %SWARM_IFINDEX%>>"%LOG_FILE%"
echo Mode: %MODE%>>"%LOG_FILE%"
echo.>>"%LOG_FILE%"
ipconfig /all >>"%LOG_FILE%" 2>&1

echo [1/4] Flushing DNS cache...
ipconfig /flushdns >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo WARNING: DNS cache flush returned an error. See log.
) else (
    echo DNS cache flush command completed.
)

echo [2/4] Restarting selected adapter...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $i=0; if(-not [int]::TryParse($env:SWARM_IFINDEX,[ref]$i)){throw 'Invalid interface index'}; $a=Get-NetAdapter -InterfaceIndex $i -ErrorAction Stop; Write-Output ('Restarting ' + $a.Name); Restart-NetAdapter -InputObject $a -Confirm:$false -ErrorAction Stop; Start-Sleep -Seconds 3; $a=Get-NetAdapter -InterfaceIndex $i -ErrorAction Stop; Write-Output ('Adapter status after restart: ' + $a.Status) } catch { Write-Error $_; exit 1 }" >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo ERROR: Adapter restart failed. See log.
    goto :repair_failed
)
echo Adapter restart command completed.

echo [3/4] Checking DHCP configuration...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $i=0; if(-not [int]::TryParse($env:SWARM_IFINDEX,[ref]$i)){throw 'Invalid interface index'}; $a=Get-NetAdapter -InterfaceIndex $i -ErrorAction Stop; $dhcp=@(Get-NetIPInterface -InterfaceIndex $i -ErrorAction SilentlyContinue | Where-Object {$_.Dhcp -eq 'Enabled'}); if($dhcp.Count -gt 0){ Write-Output ('DHCP enabled; renewing lease for adapter ' + $a.Name); & ipconfig.exe /renew $a.Name; if($LASTEXITCODE -ne 0){exit $LASTEXITCODE} } else { Write-Output 'DHCP is disabled for this adapter; preserving its static configuration.' } } catch { Write-Error $_; exit 1 }" >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo WARNING: DHCP renewal returned an error or the adapter has no DHCP lease.
    echo Review the log before trying another repair.
) else (
    echo DHCP check completed.
)

if "%MODE%"=="2" goto :winsock_prompt
goto :verify_adapter

:winsock_prompt
echo.
echo Winsock reset affects the entire Windows networking stack.
echo VPN clients, endpoint security software and other network tools may
echo need repair afterwards. A Windows restart may be required.
set /p "WINSOCK_CONFIRM=Type RESET to also reset Winsock, or press Enter to skip: "
if /i "%WINSOCK_CONFIRM%"=="RESET" goto :winsock_reset
echo Winsock reset skipped. Standard adapter repair remains complete.
echo Winsock reset skipped by user.>>"%LOG_FILE%"
goto :verify_adapter

:winsock_reset
echo [4/4] Resetting Winsock catalog...
echo Winsock reset confirmed.>>"%LOG_FILE%"
netsh winsock reset >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo ERROR: Winsock reset returned an error. See log.
    goto :repair_failed
)
echo Winsock reset command completed. Restart Windows when convenient.

:verify_adapter

echo.
echo Verifying adapter state...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $i=0; if(-not [int]::TryParse($env:SWARM_IFINDEX,[ref]$i)){throw 'Invalid interface index'}; Get-NetAdapter -InterfaceIndex $i -ErrorAction Stop | Format-List Name,InterfaceDescription,Status,LinkSpeed,ifIndex } catch { Write-Error $_; exit 1 }" >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo WARNING: Could not read the final adapter state. See log.
) else (
    echo Final adapter state was written to the log.
)

echo.
echo ============================================================
echo Repair sequence finished.
echo Log file: "%LOG_FILE%"
echo ============================================================
echo.
pause
exit /b 0

:invalid_adapter
echo.
echo ERROR: The selected Interface Index is invalid or unavailable.
pause
exit /b 2

:invalid_mode
echo.
echo ERROR: Choose 1, 2 or Q.
pause
exit /b 2

:cancelled
echo.
echo No network changes were made.
pause
exit /b 0

:repair_failed
echo.
echo Repair stopped after an error. Review the log:
echo "%LOG_FILE%"
pause
exit /b 1
