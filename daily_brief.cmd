@echo off
REM printmoney daily brief.
REM
REM Writes four things into reports\ :
REM   printmoney.ics       a subscribable calendar that grows one entry a day
REM   brief.html           a phone-sized page of the same thing
REM   brief_YYYYMMDD.txt   the terminal output, kept as a record
REM   brief_YYYYMMDD.log   the run log (informational, not errors)
REM   brief_YYYYMMDD.json  the same data for anything else to read
REM
REM Install it to run every morning:
REM   schtasks /create /tn "printmoney daily" /tr "D:\Print-Money\daily_brief.cmd" /sc daily /st 08:00
REM
REM Remove it again:
REM   schtasks /delete /tn "printmoney daily" /f
REM
REM To reach it from a phone, put reports\ inside OneDrive or iCloud Drive, share
REM printmoney.ics as a link, then on iOS:
REM   Calendar -> Calendars -> Add Calendar -> Add Subscription Calendar -> paste
REM The phone refreshes it on its own. Nothing to install.

setlocal
cd /d "%~dp0"
if not exist "reports" mkdir "reports"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set STAMP=%%i

set PYTHONIOENCODING=utf-8
python pm.py daily --ics "reports\printmoney.ics" --html "reports\brief.html" > "reports\brief_%STAMP%.txt" 2> "reports\brief_%STAMP%.log"

python pm.py daily --json --no-carry > "reports\brief_%STAMP%.json" 2>nul

if errorlevel 1 (
  echo Brief failed. See reports\brief_%STAMP%.log
) else (
  echo Wrote reports\brief_%STAMP%.txt and reports\printmoney.ics
)
endlocal
