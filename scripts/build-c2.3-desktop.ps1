param(
  [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dotnetRoot = "F:\DevTools\dotnet"
$dotnet = Join-Path $dotnetRoot "dotnet.exe"
$solution = Join-Path $projectRoot "desktop-host\PenguinConvexity.Desktop.slnx"
$appProject = Join-Path $projectRoot "desktop-host\PenguinConvexity.Desktop\PenguinConvexity.Desktop.csproj"
$testProject = Join-Path $projectRoot "desktop-host\PenguinConvexity.Desktop.Tests\PenguinConvexity.Desktop.Tests.csproj"
$publishRoot = Join-Path $projectRoot "desktop-host\publish\win-x64"

if (-not (Test-Path -LiteralPath $dotnet)) {
  throw ".NET 10 SDK not found at $dotnet"
}

$env:DOTNET_ROOT = $dotnetRoot
$env:PATH = "$dotnetRoot;$env:PATH"
$env:NUGET_PACKAGES = "F:\DevTools\nuget-packages"
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"

& $dotnet restore $solution --locked-mode
if ($LASTEXITCODE -ne 0) { throw "C2.3 package restore failed: $LASTEXITCODE" }

& $dotnet build $solution -c Release --no-restore
if ($LASTEXITCODE -ne 0) { throw "C2.3 desktop build failed: $LASTEXITCODE" }

if (-not $SkipTests) {
  & $dotnet test $testProject -c Release --no-build --no-restore --logger "console;verbosity=minimal"
  if ($LASTEXITCODE -ne 0) { throw "C2.3 desktop tests failed: $LASTEXITCODE" }
}

& $dotnet publish $appProject -c Release -r win-x64 --self-contained true --no-restore -o $publishRoot
if ($LASTEXITCODE -ne 0) { throw "C2.3 self-contained publish failed: $LASTEXITCODE" }

$exe = Join-Path $publishRoot "PenguinConvexity.Desktop.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Published desktop executable is missing: $exe" }

[PSCustomObject]@{
  status = "success"
  sdk = (& $dotnet --version)
  runtime = "win-x64"
  selfContained = $true
  executable = $exe
  bytes = (Get-Item -LiteralPath $exe).Length
} | ConvertTo-Json
