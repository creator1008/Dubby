# Restart Dubby API (uvicorn) and worker with latest code.
$ErrorActionPreference = "Stop"
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object {
    $_.CommandLine -like '*uvicorn app.main*' -or
    $_.CommandLine -like '*app.worker.runner*'
  }
foreach ($p in $procs) {
  Write-Output ("Stopping PID=" + $p.ProcessId)
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
Set-Location "D:\Coding\Dubby\api"
Start-Process -NoNewWindow -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000"
Start-Sleep -Seconds 3
Start-Process -NoNewWindow -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "-m","app.worker.runner"
Start-Sleep -Seconds 3
try {
  $hz = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 8
  Write-Output ("healthz=" + $hz.StatusCode + " " + $hz.Content)
} catch {
  Write-Output ("healthz_error=" + $_.Exception.Message)
}
try {
  $doc = Invoke-WebRequest -Uri "http://127.0.0.1:8000/openapi.json" -UseBasicParsing -TimeoutSec 8
  $paths = ($doc.Content | ConvertFrom-Json).paths.PSObject.Properties.Name |
    Where-Object { $_ -like '/v1/voices*' }
  Write-Output ("voice_paths=" + ($paths -join ','))
} catch {
  Write-Output ("openapi_error=" + $_.Exception.Message)
}
