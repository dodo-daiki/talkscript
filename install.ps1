<#
.SYNOPSIS
  talkscript のインストーラー（Windows）。

.DESCRIPTION
  足りないものを調べ、1 つずつ確認してから入れる。
    1. uv（Python とパッケージの管理。https://docs.astral.sh/uv/）
    2. Python（uv が入れる。PC にすでにある Python には手を付けない）
    3. talkscript 本体（uv tool install。コマンド talkscript が使えるようになる）
    4. talkscript setup: VOICEVOX（利用規約への同意を求めてから）、PC ごとの設定、作業フォルダ
  管理者の権限は要らない（すべて利用者のフォルダに入れる）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1
  powershell -ExecutionPolicy Bypass -File install.ps1 -Yes -NoVoicevox
  powershell -ExecutionPolicy Bypass -File install.ps1 -Source C:\path\to\talkscript   # 手元のフォルダから入れる
#>
param(
    [switch]$Yes,                 # 確認をすべて「はい」にする
    [string]$Source = "",         # talkscript の入手元（既定: GitHub Releases の wheel）。フォルダか URL
    [switch]$NoVoicevox,          # VOICEVOX を入れない
    [string]$WorkspaceDir = ""    # 作業フォルダの場所（既定: ホームの talkscript-work）
)
$ErrorActionPreference = "Stop"
$TalkscriptVersion = "0.1.2"
$UvVersion = "0.13.0"
$PythonVersion = "3.12"
if (-not $Source) { $Source = "https://github.com/dodo-daiki/talkscript/releases/download/v$TalkscriptVersion/talkscript-$TalkscriptVersion-py3-none-any.whl" }

function Confirm-Step([string]$Message) {
    if ($Yes) { Write-Host "$Message [y/N] y（-Yes）"; return $true }
    try { $a = Read-Host "$Message [y/N]" } catch { return $false }
    return ($a -match '^(y|yes|はい)$')
}
function Stop-Install([string]$Message) {
    Write-Host "中止: $Message" -ForegroundColor Yellow
    exit 1
}

Write-Host "talkscript $TalkscriptVersion のインストール（入手元: $Source）"

# --- 1. uv -------------------------------------------------------------------------------------
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv -and (Test-Path "$env:USERPROFILE\.local\bin\uv.exe")) { $uv = "$env:USERPROFILE\.local\bin\uv.exe" }
if ($uv) {
    Write-Host "[済] uv: $(& $uv --version)"
} else {
    Write-Host "[不足] uv が見つからない。"
    Write-Host "       入れるもの: uv $UvVersion（Astral 社の公式のインストーラー https://astral.sh/uv/$UvVersion/install.ps1）"
    Write-Host "       置き場所: $env:USERPROFILE\.local\bin（利用者の PATH に足される）"
    if (-not (Confirm-Step "uv を入れますか？")) { Stop-Install "uv がないと続けられない" }
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/$UvVersion/install.ps1 | iex"
    if ($LASTEXITCODE -ne 0) { Stop-Install "uv を入れられなかった" }
    $uv = "$env:USERPROFILE\.local\bin\uv.exe"
    if (-not (Test-Path $uv)) { Stop-Install "uv が $uv にない" }
    Write-Host "[済] uv: $(& $uv --version)"
}

# --- 2. Python ---------------------------------------------------------------------------------
$prevEap = $ErrorActionPreference; $ErrorActionPreference = "Continue"   # PowerShell 5.1 は外部コマンドの標準エラーを例外にするため
$py = & $uv python find $PythonVersion 2>$null
$found = ($LASTEXITCODE -eq 0 -and $py)
$ErrorActionPreference = $prevEap
if ($found) {
    Write-Host "[済] Python ${PythonVersion}: $py"
} else {
    Write-Host "[不足] Python $PythonVersion が見つからない。"
    Write-Host "       入れるもの: uv が管理する Python $PythonVersion（python-build-standalone、数十 MB）。PC の既存の Python には手を付けない"
    if (-not (Confirm-Step "Python $PythonVersion を入れますか？")) { Stop-Install "Python がないと続けられない" }
    & $uv python install $PythonVersion
    if ($LASTEXITCODE -ne 0) { Stop-Install "Python を入れられなかった" }
}

# --- 3. talkscript 本体 -------------------------------------------------------------------------
Write-Host "talkscript を入れる（uv tool install。依存: PyYAML、sounddevice（PortAudio を同梱））"
& $uv tool install --force --python $PythonVersion --from $Source talkscript
if ($LASTEXITCODE -ne 0) { Stop-Install "talkscript を入れられなかった" }
$bin = (& $uv tool dir --bin).Trim()
$ts = Join-Path $bin "talkscript.exe"
if (-not (Test-Path $ts)) { Stop-Install "talkscript のコマンドが $ts にない" }
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not (($env:Path -split ';') -contains $bin) -and -not (($userPath -split ';') -contains $bin)) {
    Write-Host "[不足] コマンドの場所 $bin が PATH に入っていない（入れないと、毎回この場所を指定して実行することになる）。"
    if (Confirm-Step "$bin を利用者の PATH に足しますか？") {
        & $uv tool update-shell
        Write-Host "       PATH に足した（新しく開いた端末から有効）"
    }
}

# --- 4. VOICEVOX・PC ごとの設定・作業フォルダ（talkscript setup が確認を求める） --------------------------
$setupArgs = @("setup")
if ($Yes) { $setupArgs += "--yes" }
if ($NoVoicevox) { $setupArgs += "--no-voicevox" }
if ($WorkspaceDir) { $setupArgs += @("--workspace-dir", $WorkspaceDir) }
& $ts @setupArgs
$code = $LASTEXITCODE
Write-Host ""
Write-Host "talkscript のコマンド: $ts"
if ($code -ne 0) { Write-Host "足りないものが残っている（上の NG）。talkscript doctor で確かめられる" -ForegroundColor Yellow }
exit $code
