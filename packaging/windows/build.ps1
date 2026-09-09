param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$releaseVenv = Join-Path $repoRoot ".venv-release"
$releasePython = Join-Path $releaseVenv "Scripts\python.exe"
$spec = Join-Path $repoRoot "packaging\windows\kicad-flow.spec"
$exe = Join-Path $repoRoot "dist\kicad-flow\kicad-flow.exe"

if (-not (Test-Path -LiteralPath $releasePython)) {
    python -m venv $releaseVenv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the release environment" }
}
if (-not $SkipInstall) {
    & $releasePython -m pip install --disable-pip-version-check -e "$repoRoot[release]"
    if ($LASTEXITCODE -ne 0) { throw "Failed to install release dependencies" }
}

& $releasePython -m PyInstaller --noconfirm --clean $spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination (Split-Path $exe)
Copy-Item -LiteralPath (Join-Path $repoRoot "README.md") -Destination (Split-Path $exe)
& $exe --version
if ($LASTEXITCODE -ne 0) { throw "Packaged --version failed" }
& $exe doctor
if ($LASTEXITCODE -ne 0) { throw "Packaged doctor failed" }
& $releasePython (Join-Path $repoRoot "packaging\windows\smoke.py") $exe
if ($LASTEXITCODE -ne 0) { throw "Packaged stdio MCP smoke test failed" }

Write-Host "Release ready: $exe"
