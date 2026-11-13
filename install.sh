#!/bin/sh
# talkscript のインストーラー（Linux）。
#
#   sh install.sh [--yes] [--source <フォルダか URL>] [--no-voicevox] [--workspace-dir <フォルダ>]
#   curl -LsSf https://raw.githubusercontent.com/dodo-daiki/talkscript/main/install.sh | sh
#
# 足りないものを調べ、1 つずつ確認してから入れる:
#   1. curl（uv を取ってくるため。apt で入れる）
#   2. PortAudio（libportaudio2。音声の出力に使う。apt で入れる。sudo が要る）
#   3. uv（Python とパッケージの管理。https://docs.astral.sh/uv/。~/.local/bin に入る）
#   4. Python（uv が入れる。システムの Python には手を付けない）
#   5. talkscript 本体（uv tool install）
#   6. talkscript setup: VOICEVOX（利用規約への同意を求めてから）、PC ごとの設定、作業フォルダ
# apt のない配布物では、1・2 の入れ方を表示して止まる。
set -eu

TALKSCRIPT_VERSION=0.1.2
UV_VERSION=0.13.0
PYTHON_VERSION=3.12
YES=0
SOURCE=""
NO_VOICEVOX=0
WORKSPACE_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y) YES=1 ;;
        --source) SOURCE="$2"; shift ;;
        --no-voicevox) NO_VOICEVOX=1 ;;
        --workspace-dir) WORKSPACE_DIR="$2"; shift ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "知らない引数: $1" >&2; exit 2 ;;
    esac
    shift
done
[ -n "$SOURCE" ] || SOURCE="https://github.com/dodo-daiki/talkscript/releases/download/v${TALKSCRIPT_VERSION}/talkscript-${TALKSCRIPT_VERSION}-py3-none-any.whl"

confirm() {
    if [ "$YES" = 1 ]; then
        echo "$1 [y/N] y（--yes）"
        return 0
    fi
    # curl … | sh で実行したときは標準入力がスクリプトなので、端末から読む
    if ! { printf '%s [y/N] ' "$1" > /dev/tty && read -r ans < /dev/tty; } 2>/dev/null; then
        echo "$1 → 対話できないので行わない（行うなら --yes）"
        return 1
    fi
    case "$ans" in y|Y|yes|YES|はい) return 0 ;; *) return 1 ;; esac
}
die() { echo "中止: $1" >&2; exit 1; }

SUDO=""
if [ "$(id -u)" != 0 ]; then
    command -v sudo > /dev/null 2>&1 && SUDO="sudo" || SUDO=""
fi
APT_UPDATED=0
apt_install() {   # apt_install <説明> <パッケージ…>
    what="$1"; shift
    if ! command -v apt-get > /dev/null 2>&1; then
        die "$what がない。この配布物のパッケージ管理で $* にあたるものを入れてから、もう一度実行する"
    fi
    if [ "$(id -u)" != 0 ] && [ -z "$SUDO" ]; then
        die "$what を入れるには管理者の権限（sudo）が要る。管理者に $* を入れてもらう"
    fi
    echo "       入れるもの: $*（apt-get install。${SUDO:+sudo で}管理者の権限を使う）"
    confirm "$what を入れますか？" || die "$what がないと続けられない"
    if [ "$APT_UPDATED" = 0 ]; then
        $SUDO apt-get update -q || die "apt-get update に失敗した"
        APT_UPDATED=1
    fi
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -q "$@" || die "$* を入れられなかった"
}

echo "talkscript $TALKSCRIPT_VERSION のインストール（入手元: $SOURCE）"
[ "$(uname -s)" = Linux ] || die "Linux 用のインストーラー（Windows は install.ps1）"
case "$(uname -m)" in
    x86_64|aarch64|arm64) ;;
    *) echo "注意: この CPU（$(uname -m)）は試していない" ;;
esac

# --- 1. curl --------------------------------------------------------------------------------
if command -v curl > /dev/null 2>&1; then
    echo "[済] curl"
else
    echo "[不足] curl が見つからない（uv の公式のインストーラーを取ってくるのに使う）。"
    apt_install "curl" curl ca-certificates
fi

# --- 2. PortAudio ---------------------------------------------------------------------------
has_portaudio() {
    ldconfig -p 2>/dev/null | grep -q 'libportaudio\.so\.2' && return 0
    for d in /usr/lib /usr/lib64 /usr/lib/x86_64-linux-gnu /usr/lib/aarch64-linux-gnu /usr/local/lib; do
        ls "$d"/libportaudio.so.2* > /dev/null 2>&1 && return 0
    done
    return 1
}
if has_portaudio; then
    echo "[済] PortAudio（libportaudio2）"
else
    echo "[不足] PortAudio（libportaudio2）が見つからない（音声を時刻どおりに出すのに使う）。"
    apt_install "PortAudio" libportaudio2
fi

# --- 3. uv ----------------------------------------------------------------------------------
UV="$(command -v uv 2>/dev/null || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -n "$UV" ]; then
    echo "[済] uv: $("$UV" --version)"
else
    echo "[不足] uv が見つからない。"
    echo "       入れるもの: uv $UV_VERSION（Astral 社の公式のインストーラー https://astral.sh/uv/$UV_VERSION/install.sh）"
    echo "       置き場所: $HOME/.local/bin（シェルの設定ファイルに PATH が足される）"
    confirm "uv を入れますか？" || die "uv がないと続けられない"
    curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh || die "uv を入れられなかった"
    UV="$HOME/.local/bin/uv"
    [ -x "$UV" ] || die "uv が $UV にない"
    echo "[済] uv: $("$UV" --version)"
fi

# --- 4. Python ------------------------------------------------------------------------------
if PY="$("$UV" python find "$PYTHON_VERSION" 2>/dev/null)"; then
    echo "[済] Python $PYTHON_VERSION: $PY"
else
    echo "[不足] Python $PYTHON_VERSION が見つからない。"
    echo "       入れるもの: uv が管理する Python $PYTHON_VERSION（python-build-standalone、数十 MB）。システムの Python には手を付けない"
    confirm "Python $PYTHON_VERSION を入れますか？" || die "Python がないと続けられない"
    "$UV" python install "$PYTHON_VERSION" || die "Python を入れられなかった"
fi

# --- 5. talkscript 本体 ----------------------------------------------------------------------
echo "talkscript を入れる（uv tool install。依存: PyYAML、sounddevice）"
"$UV" tool install --force --python "$PYTHON_VERSION" --from "$SOURCE" talkscript || die "talkscript を入れられなかった"
BIN="$("$UV" tool dir --bin)"
TS="$BIN/talkscript"
[ -x "$TS" ] || die "talkscript のコマンドが $TS にない"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *)
        echo "[不足] コマンドの場所 $BIN が PATH に入っていない（入れないと、毎回この場所を指定して実行することになる）。"
        if confirm "$BIN をシェルの設定ファイルの PATH に足しますか？"; then
            if "$UV" tool update-shell; then
                echo "       PATH に足した（新しく開いた端末から有効）"
            else
                echo "       自動では足せなかった。シェルの設定ファイル（例 ~/.bashrc）に次の 1 行を足す:"
                echo "         export PATH=\"$BIN:\$PATH\""
            fi
        fi
        ;;
esac

# --- 6. VOICEVOX・PC ごとの設定・作業フォルダ（talkscript setup が確認を求める） ------------------------
set -- setup
[ "$YES" = 1 ] && set -- "$@" --yes
[ "$NO_VOICEVOX" = 1 ] && set -- "$@" --no-voicevox
[ -n "$WORKSPACE_DIR" ] && set -- "$@" --workspace-dir "$WORKSPACE_DIR"
code=0
if [ "$YES" != 1 ] && [ ! -t 0 ] && { : < /dev/tty; } 2>/dev/null; then
    "$TS" "$@" < /dev/tty || code=$?          # curl … | sh のときも、確認を端末から読めるようにする
else
    "$TS" "$@" || code=$?
fi
echo ""
echo "talkscript のコマンド: $TS"
[ "$code" = 0 ] || echo "足りないものが残っている（上の NG）。talkscript doctor で確かめられる"
exit "$code"
