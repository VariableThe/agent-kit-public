$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'install.py'
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run --no-project python $script install @args
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $script install @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $script install @args
} else {
    throw 'Install uv or Python 3.10+ first.'
}
exit $LASTEXITCODE
