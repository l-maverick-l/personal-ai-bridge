param(
    [string]$PythonExe = "python"
)

Write-Host "Installing requirements..."
& $PythonExe -m pip install -r requirements.txt

Write-Host "Packaging scaffold for Windows via PyInstaller can be added in Phase 2."
Write-Host "Current verification command: python -m app.main"
