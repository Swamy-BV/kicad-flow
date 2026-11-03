param(
    [string]$InstallationRoot = (Join-Path $env:RUNNER_TEMP 'kicad11'),
    [string]$ConfigRoot = (Join-Path $env:RUNNER_TEMP 'kicad11-config')
)

$ErrorActionPreference = 'Stop'
$build = '10.99.0.6258.gff9515f1e9'
$installer = Join-Path $env:RUNNER_TEMP "kicad-nightly-$build-x86_64.exe"
$cli = Join-Path $InstallationRoot 'bin/kicad-cli.exe'
if (-not (Test-Path -LiteralPath $cli)) {
    $url = "https://mirrors.mit.edu/kicad/windows/nightly/kicad-nightly-$build-x86_64.exe"
    Invoke-WebRequest -Uri $url -OutFile $installer
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne 'Valid' -or
        $signature.SignerCertificate.Subject -notmatch 'CN=KICAD SERVICES CORPORATION(?:,|$)') {
        throw 'The nightly installer does not have a valid KiCad publisher signature.'
    }
    $install = Start-Process -FilePath $installer -ArgumentList '/S', "/D=$InstallationRoot" `
        -WindowStyle Hidden -Wait -PassThru
    if ($install.ExitCode -ne 0) { throw "KiCad installer failed: $($install.ExitCode)" }
}
$version = & $cli version
if ($LASTEXITCODE -ne 0) { throw 'KiCad version query failed.' }
$commit = & $cli version --format commit
if ($LASTEXITCODE -ne 0 -or $version -ne '10.99.0' -or
    $commit -ne 'ff9515f1e9607d7e68f0ebd568f4758318690e0e') {
    throw "Unexpected KiCad build: $version ($commit)"
}

$dataRoot = Join-Path $InstallationRoot 'share/kicad'
$symbols = Join-Path $dataRoot 'symbols'
$footprints = Join-Path $dataRoot 'footprints'
foreach ($required in @($symbols, $footprints)) {
    if (-not (Test-Path -LiteralPath $required -PathType Container)) {
        throw "KiCad's full library package is required: $required"
    }
}
$config = Join-Path $ConfigRoot '10.99'
New-Item -ItemType Directory -Path $config -Force | Out-Null
@{ api = @{ enable_server = $true } } | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $config 'kicad_common.json') -Encoding utf8

# Build only library configuration from installed resources. Project documents
# are created and saved by the native editor in the integration examples.
$symbolRows = Get-ChildItem -LiteralPath $symbols |
    Where-Object { $_.Extension -in '.kicad_sym', '.kicad_symdir' } |
    Sort-Object Name | ForEach-Object {
        $nickname = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
        '(lib (name "{0}") (type "KiCad") (uri "{1}") (options "") (descr ""))' -f `
            $nickname, $_.FullName.Replace('\', '/')
    }
"(sym_lib_table (version 7)`n$($symbolRows -join "`n")`n)" |
    Set-Content -LiteralPath (Join-Path $config 'sym-lib-table') -Encoding utf8
$footprintRows = Get-ChildItem -LiteralPath $footprints -Directory -Filter '*.pretty' |
    Sort-Object Name | ForEach-Object {
        $nickname = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
        '(lib (name "{0}") (type "KiCad") (uri "{1}") (options "") (descr ""))' -f `
            $nickname, $_.FullName.Replace('\', '/')
    }
"(fp_lib_table (version 7)`n$($footprintRows -join "`n")`n)" |
    Set-Content -LiteralPath (Join-Path $config 'fp-lib-table') -Encoding utf8
$settings = @{
    KICAD_FLOW_KICAD_CLI = $cli
    KICAD_CONFIG_HOME = $ConfigRoot
    KICAD_SYMBOL_DIR = $symbols
    KICAD11_SYMBOL_DIR = $symbols
    KICAD11_FOOTPRINT_DIR = $footprints
    KICAD11_3DMODEL_DIR = (Join-Path $dataRoot '3dmodels')
}
foreach ($setting in $settings.GetEnumerator()) {
    "$($setting.Key)=$($setting.Value)" | Add-Content -LiteralPath $env:GITHUB_ENV
}
Write-Output "Configured native KiCad $version"
