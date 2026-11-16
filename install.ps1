<#
.SYNOPSIS
  talkscript のインストーラー（Windows）。

.DESCRIPTION
  足りないものを調べ、1 つずつ確認してから入れる。
    1. uv（Python とパッケージの管理。https://docs.astral.sh/uv/）
    2. Python（uv が入れる。PC にすでにある Python には手を付けない）
    3. talkscript 本体（uv tool install。コマンド talkscript が使えるようになる）
    4. talkscript setup: VOICEVOX（利用規約への同意を求めてから）、PC ごとの設定、作業フォルダ、画面を開くショートカット
    5. Windows の「アプリ」の一覧への登録（設定 → アプリ から、アンインストールできるように）
  管理者の権限は要らない（すべて利用者のフォルダに入れる）。
  すでに入っているときは、「入れ直す・更新する」か「アンインストールする」かを尋ねる。
  -Uninstall: アンインストールする。talkscript のコマンドと画面・ショートカット・アイコン・一覧の登録を消し、
  talkscript が入れた VOICEVOX と PC ごとの設定は尋ねてから消す。作業フォルダ（台本・実行の記録）と uv・Python は残す。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1
  powershell -ExecutionPolicy Bypass -File install.ps1 -Yes -NoVoicevox
  powershell -ExecutionPolicy Bypass -File install.ps1 -Source C:\path\to\talkscript   # 手元のフォルダから入れる
  powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
#>
param(
    [switch]$Yes,                 # 確認をすべて「はい」にする
    [string]$Source = "",         # talkscript の入手元（既定: GitHub Releases の wheel）。フォルダか URL
    [switch]$NoVoicevox,          # VOICEVOX を入れない
    [string]$WorkspaceDir = "",   # 作業フォルダの場所（既定: ホームの talkscript-work）
    [switch]$Uninstall,           # アンインストールする
    [switch]$Update               # 画面からの更新（入っていても尋ねずに入れ直す）
)
$ErrorActionPreference = "Stop"
$TalkscriptVersion = "0.2.4"
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

$keyName = if ($env:TALKSCRIPT_UNINSTALL_KEY) { $env:TALKSCRIPT_UNINSTALL_KEY } else { "talkscript" }   # 試験では別の名前にする
$RegKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$keyName"
$UninstallDir = Join-Path $env:LOCALAPPDATA "talkscript\uninstall"

function Find-Uv {
    $u = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $u -and (Test-Path "$env:USERPROFILE\.local\bin\uv.exe")) { $u = "$env:USERPROFILE\.local\bin\uv.exe" }
    return $u
}
function Invoke-Native([scriptblock]$Block) {   # PowerShell 5.1 は外部コマンドの標準エラーを例外にするため
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Block } finally { $ErrorActionPreference = $prev }
}
function Get-Installed([string]$UvPath) {   # 入っている talkscript の「talkscript v0.2.1」の行（なければ $null）
    if (-not $UvPath) { return $null }
    return (Invoke-Native { & $UvPath tool list 2>$null }) | Where-Object { $_ -match '^talkscript v' } | Select-Object -First 1
}

function Invoke-Uninstall {
    Write-Host "talkscript のアンインストール"
    $uvPath = Find-Uv
    $tsExe = $null
    if ($uvPath) {
        $b = (Invoke-Native { & $uvPath tool dir --bin 2>$null })
        if ($b) { $c = Join-Path ([string]$b).Trim() "talkscript.exe"; if (Test-Path $c) { $tsExe = $c } }
    }
    Write-Host "  消すもの: talkscript のコマンドと画面、ショートカットとアイコン、「アプリ」の一覧の登録"
    Write-Host "  尋ねてから消すもの: talkscript が入れた VOICEVOX、PC ごとの設定"
    Write-Host "  残すもの: 作業フォルダ（台本・実行の記録）、uv、Python"
    if (-not (Confirm-Step "talkscript をアンインストールしますか？")) { Write-Host "やめました"; exit 0 }
    if ($tsExe) {
        $a = @("uninstall"); if ($Yes) { $a += "--yes" }
        & $tsExe @a
        if ($LASTEXITCODE -ne 0) { Stop-Install "talkscript uninstall が止まった（上の表示を確かめる）" }
    } else {   # コマンドがもうないとき: ショートカットとアイコンだけ消す
        $desk = if ($env:TALKSCRIPT_DESKTOP_DIR) { $env:TALKSCRIPT_DESKTOP_DIR } else { [Environment]::GetFolderPath("Desktop") }
        foreach ($p in @((Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\talkscript.lnk"),
                         (Join-Path $desk "talkscript.lnk"), (Join-Path $env:LOCALAPPDATA "talkscript\talkscript.ico"))) {
            if (Test-Path -LiteralPath $p) { Remove-Item -Force -LiteralPath $p; Write-Host "[済] 消した: $p" }
        }
    }
    if (Get-Installed $uvPath) {
        Invoke-Native { & $uvPath tool uninstall talkscript }
        if ($LASTEXITCODE -ne 0) { Stop-Install "talkscript のコマンドを消せなかった（画面を閉じてから、もう一度実行する）" }
        Write-Host "[済] talkscript のコマンドと画面を消した"
    }
    if (Test-Path $RegKey) { Remove-Item -Recurse -Force $RegKey; Write-Host "[済] 「アプリ」の一覧から外した" }
    if (Test-Path -LiteralPath $UninstallDir) {   # アンインストール用に置いたもの（この写し）は、この窓が閉じてから消す
        $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId
        $q = $UninstallDir.Replace("'", "''")
        $cmd = "Wait-Process -Id $parent -ErrorAction SilentlyContinue; Remove-Item -Recurse -Force -LiteralPath '$q' -ErrorAction SilentlyContinue; " +
               "`$d = Split-Path -LiteralPath '$q'; if ((Test-Path -LiteralPath `$d) -and -not (Get-ChildItem -LiteralPath `$d -Force)) { Remove-Item -LiteralPath `$d }"
        Start-Process powershell -WindowStyle Hidden -ArgumentList @("-NoProfile", "-Command", $cmd)
    }
    Write-Host "[残す] uv と Python（ほかのものも使えるため）。要らなければ: uv python uninstall $PythonVersion。uv 自体の消し方は https://docs.astral.sh/uv/"
    Write-Host "talkscript をアンインストールしました"
    exit 0
}

if ($Uninstall) { Invoke-Uninstall }

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

# すでに入っているとき: 入れ直す（更新する）か、アンインストールするか
if (-not $Update -and -not $Yes) {
    $cur = Get-Installed $uv
    if ($cur) {
        Write-Host ""
        Write-Host "$cur が入っています。"
        Write-Host "  [1] 入れ直す・更新する（$TalkscriptVersion）"
        Write-Host "  [2] アンインストールする"
        try { $c = Read-Host "番号を選んでください（Enter は 1、ほかはやめる）" } catch { $c = "q" }
        if ($c -eq "2") { Invoke-Uninstall }
        elseif ($c -ne "" -and $c -ne "1") { Write-Host "やめました"; exit 0 }
    }
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

# --- 5. Windows の「アプリ」の一覧に登録（設定 → アプリ から、アンインストールできるように） -------------------
if ($PSCommandPath) {
    try {
        New-Item -ItemType Directory -Force $UninstallDir | Out-Null
        Copy-Item -Force -LiteralPath $PSCommandPath (Join-Path $UninstallDir "install.ps1")
        $cmdFile = Join-Path $UninstallDir "uninstall.cmd"
        # 1 行で書く（消している間に cmd が次の行を読みに行かないように）
        [IO.File]::WriteAllText($cmdFile, "@powershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0install.ps1`" -Uninstall & echo. & pause & exit /b`r`n", [Text.Encoding]::ASCII)
        New-Item -Force $RegKey | Out-Null
        Set-ItemProperty $RegKey DisplayName "talkscript"
        Set-ItemProperty $RegKey DisplayVersion $TalkscriptVersion
        Set-ItemProperty $RegKey Comments "発話と動作の台本ツール"
        Set-ItemProperty $RegKey URLInfoAbout "https://github.com/dodo-daiki/talkscript"
        Set-ItemProperty $RegKey InstallLocation $UninstallDir
        Set-ItemProperty $RegKey UninstallString "`"$env:ComSpec`" /c `"$cmdFile`""
        Set-ItemProperty $RegKey NoModify 1 -Type DWord
        Set-ItemProperty $RegKey NoRepair 1 -Type DWord
        $ico = Join-Path $env:LOCALAPPDATA "talkscript\talkscript.ico"
        if (Test-Path $ico) { Set-ItemProperty $RegKey DisplayIcon $ico }
        Write-Host "[済] Windows の「アプリ」の一覧に登録した（設定 → アプリ から、アンインストールできる）"
    } catch {
        Write-Host "[注意] 「アプリ」の一覧に登録できなかった: $_（アンインストールは、インストーラーを開いて 2 を選ぶ）" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "talkscript の画面を開くには: デスクトップかスタートメニューの talkscript か、端末で talkscript（引数なし）"
Write-Host "talkscript のコマンド: $ts"
if ($code -ne 0) { Write-Host "足りないものが残っている（上の NG）。talkscript doctor で確かめられる" -ForegroundColor Yellow }
exit $code
