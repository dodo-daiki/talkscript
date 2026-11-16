# <img src="talkscript/assets/talkscript.png" width="40" height="40" alt=""> talkscript

## ダウンロード

| OS | 入れ方 |
|---|---|
| Windows | **[talkscript-setup.exe をダウンロード](https://github.com/dodo-daiki/talkscript/releases/latest/download/talkscript-setup.exe)** して、ダブルクリック |
| Linux | 端末で `curl -LsSf https://raw.githubusercontent.com/dodo-daiki/talkscript/main/install.sh \| sh` |

Windows のインストーラーの URL（いつも最新の版を指します）:
<https://github.com/dodo-daiki/talkscript/releases/latest/download/talkscript-setup.exe>

これまでの版と変更点: <https://github.com/dodo-daiki/talkscript/releases>。
入れ方の詳しい説明は、下の「[インストール](#インストール)」にあります。

## talkscript とは

台詞（[VOICEVOX](https://voicevox.hiroshiba.jp/) で合成）と、関節ごとのキーフレームで書いた動作を 1 つの台本（YAML）にまとめ、
ロボットを発話に合わせて動かすための道具です。

- 動作のキーフレームは、時刻でも、発話の位置（句・モーラ・文の一部の始まりか終わり、またはモーラの途中）でも置けます。
  発話の位置に置いたキーフレームは、話者・話速・文を変えて合成し直しても発話についていきます。
- 実行の前に、関節の範囲・角速度・角加速度の上限、休止姿勢で始まり休止姿勢で終わるか、などを検査します。上限を超える軌道は切り詰めずに止めます。
- 音声は、PC の音声出力に時刻を指定して出します。ロボットには同じ開始時刻を共有させます。実際の時刻はすべて記録します。
- 対応するロボット:
  - `self7`: 7 関節の自作機。Raspberry Pi の上の talkscript-agent に TCP/JSON で軌道を送ります
  - `sota`: Vstone 社の Sota。Sota の上で動かす RobotController（TCP 22222）に姿勢の列を送ります
  - `fake`: 試験用の擬似ロボット

対応する OS は Windows と Linux です。

## インストール

足りないもの（uv、Python、Linux では curl と PortAudio）を調べ、**1 つずつ確認を出してから**入れます。
続けて VOICEVOX（CPU 版 0.26.2、約 2.1 GB）を、利用規約への同意を求めたうえで公式の配布から入れます。
最後に作業フォルダと、画面を開くショートカットを（確認してから）作ります。管理者の権限は、Linux で apt を使うときだけ要ります。

**Windows**: [talkscript-setup.exe](https://github.com/dodo-daiki/talkscript/releases/latest/download/talkscript-setup.exe)
（最新の版のインストーラー）をダウンロードし、ダブルクリックします。窓が開き、足りないものを 1 つずつ尋ねます。
終わると、デスクトップとスタートメニューに talkscript のアイコンができます。以後はそのアイコンから画面を開きます。

- インストーラーには電子署名がないため、初めて開くと「Windows によって PC が保護されました」と出ることがあります。
  「詳細情報」を押し、「実行」を押してください。
- インストーラーは、下の install.ps1 を中に持っていて、それを実行するだけです（作り方は `tools/build_setup.py`）。
  PowerShell から直接実行しても同じです:

```powershell
irm https://raw.githubusercontent.com/dodo-daiki/talkscript/main/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File install.ps1
```

**Linux**（Debian・Ubuntu 系。apt のない配布物では、curl と PortAudio を先に入れてください）:

```sh
curl -LsSf https://raw.githubusercontent.com/dodo-daiki/talkscript/main/install.sh | sh
```

| 引数 | 内容 |
|---|---|
| `-Yes` / `--yes` | 確認をすべて「はい」にする（VOICEVOX の利用規約への同意を含む） |
| `-NoVoicevox` / `--no-voicevox` | VOICEVOX を入れない（すでに入っている場合など） |
| `-WorkspaceDir` / `--workspace-dir` | 作業フォルダの場所（既定はホームの `talkscript-work`） |
| `-Source` / `--source` | talkscript の入手元（既定は GitHub Releases のこの版の wheel。手元のフォルダや zip の URL も指定できる） |

インストールされるもの・場所:

| もの | 場所 |
|---|---|
| uv | `~/.local/bin`（Windows は `%USERPROFILE%\.local\bin`） |
| Python 3.12 | uv が管理する場所。PC に元からある Python には手を付けない |
| talkscript のコマンド | `uv tool dir --bin` の場所 |
| VOICEVOX | Windows: `%LOCALAPPDATA%\talkscript\VOICEVOX`、Linux: `~/.local/share/talkscript/VOICEVOX` |
| PC ごとの設定 | Windows: `%APPDATA%\talkscript\settings.toml`、Linux: `~/.config/talkscript/settings.toml` |

確かめるときは `talkscript doctor` を使います。

### 更新

画面を開くたびに、GitHub Releases に新しい版があるかを確かめます。あれば、画面の上に「新しい版 … があります」と出ます。
「更新する」を押すと:

- Windows: 新しい版のインストーラーをダウンロードし（sha256 を確かめます）、画面を閉じてから、インストーラーの窓で入れ直します。
  足りないものがあれば、その窓で尋ねます。終わったら、アイコンから開き直してください。
- Linux: 新しい版を入れ直します。画面を閉じて開き直すと、新しい版になります。

コマンドでは `talkscript update`（確かめるだけなら `--check`）です。ネットにつながらないときは何も出ません。
Release に sha256 の記録がないファイルでは、照合できないので更新しません。
確かめないようにするには、`settings.toml` に次のように書きます（実験の PC など）:

```toml
[update]
check = false
```

インストーラーをもう一度実行しても、更新できます（入っているものは飛ばします）。
`uv tool upgrade talkscript` では新しい版になりません（入れたときの版の wheel を指しているため）。

### アンインストール

- Windows: 「設定 → アプリ → インストールされているアプリ」の **talkscript** で「アンインストール」を押します。
  または、インストーラー（talkscript-setup.exe）をもう一度開いて「2」を選びます（`install.ps1 -Uninstall` と同じ）。
- Linux: `curl -LsSf https://raw.githubusercontent.com/dodo-daiki/talkscript/main/install.sh | sh -s -- --uninstall`

talkscript のコマンドと画面、ショートカットとアイコン、「アプリ」の一覧の登録を消します。
talkscript が入れた VOICEVOX と、PC ごとの設定は、尋ねてから消します。
**作業フォルダ（台本・実行の記録）は消しません。** uv と Python も残します（ほかのものも使えるため）。
画面で実機の実行をしている間は、アンインストールしません。

VOICEVOX をすでに入れている場合は、`%LOCALAPPDATA%\Programs\VOICEVOX`（公式のインストーラーの既定の場所）を自動で探します。
ほかの場所にあるときは、PC ごとの `settings.toml` の `[voicevox] engine_path` に `vv-engine/run(.exe)` の場所を書いてください。
起動するプログラムの場所なので、作業フォルダの `talkscript.toml`（ほかの人と共有するもの）に書いても使いません。
合成のコマンドは、エンジンが止まっていれば自動で起動します（`talkscript engine start|stop|status`）。

## 作業フォルダ

台本・ロボットの設定・組み立ての出力・実行の記録は、プログラムとは別の「作業フォルダ」に置きます。
複数の人・PC で共有するなら、作業フォルダを git で管理してください。

```
talkscript-work/
├ talkscript.toml        # 共有する設定（ロボットのアドレス、時間）
├ config/robots/*.yaml   # ロボットの設定（関節の範囲・速度の上限・休止姿勢）
├ config/gestures/*.yaml # 動作のひな形（nod, point_right）
├ scripts/*.yaml         # 台本（demo.yaml は例）
├ build/<台本>/          # 合成・組み立ての出力（WAV は .gitignore で除外）
└ runs/<実行 ID>/        # 実行の記録
```

作業フォルダは、次の順に探します。

1. `--workspace` で指定したフォルダ
2. 環境変数 `TALKSCRIPT_HOME`
3. 今いるフォルダから上へたどって、`talkscript.toml` のあるフォルダ
4. `settings.toml` の `workspace`

作業フォルダを新しく作るときは `talkscript init <フォルダ> [--default]` を使います。

設定は「同梱の既定値 < 作業フォルダの `talkscript.toml` < PC ごとの `settings.toml`」の順に重ねます。後のものが優先です。

## 使い方（画面）

スタートメニュー（Linux はアプリ一覧）の **talkscript** を開くか、端末で `talkscript` と入力します（引数なし）。
PC の中だけで動く小さなサーバ（127.0.0.1）が立ち、Edge か Chrome（Chromium）のアプリ用の窓で画面が開きます。
どちらもなければ、既定のブラウザで開きます。窓を閉じると、30 秒後にサーバも終わります（実行中は終わりません）。

| 場所 | できること |
|---|---|
| 上の帯 | 台本を選ぶ・新しく作る・**読み込む**、ブロックの切り替え・追加、保存（Ctrl+S）、合成、音声の固定、検査の結果、環境 |
| 台詞 | 台詞の追加・削除・並べ替え・間（ms）、読みの修正、聞く。書き換えて欄の外を押すと、その台詞を VOICEVOX で合成し直す |
| 声 | 話者（VOICEVOX の一覧から）と、話速・音高・抑揚・音量・前後の無音。台本全体か、行ごとに設定する |
| タイムライン | 台詞の句・モーラ・波形と同じ時間軸に、関節ごとのキーフレーム（◆）を置く。ドラッグで時刻と角度、ダブルクリックで追加。台詞のあるブロックでは、キーフレームは置いた位置のまま発話に固定され、話者・話速・文を変えてもついていく（最初の台詞より前だけは時刻）。句・モーラの境目に寄せると、その境目に吸着する（Alt で吸着しない）。上限を超えた区間は赤 |
| 姿勢の図 | 再生位置の姿勢（自作機）。再生（Space）すると、合成した音声と一緒に動く |
| 動作のひな形 | 大きさ・速さを決めて、再生位置に挿入（全ロボットに入る。重なる分は置き換え） |
| 検査 | 編集のたびに、実行の前の検査（K1〜K12）を行って表示する。押すとその場所へ移る |
| 実機で再生 | ロボット・モード・記録の項目（条件・被験者・距離・音圧・メモ）を選んで実行。操作者の合図で始めるブロックは「開始」を押す。「止める」で休止姿勢へ戻し、「すぐ止める」でトルクを切る |
| 実行の記録 | これまでの実行と要約（summary.md） |
| 環境 | 足りないものの確認（doctor）、音声の出力機器の設定、作業フォルダの変更、終了 |

### ほかで作った台本を読み込む

ほかの PC やエディタで書いた台本（`format: talkscript/script@1`）や動作のひな形（`format: talkscript/gesture@1`）の YAML は、
上の帯の **読み込む** でファイルを選ぶか、画面にファイルを落とすと、作業フォルダに取り込みます
（台本は `scripts/`、ひな形は `config/gestures/`）。取り込んだ台本はそのまま開くので、「合成」を押して声を作ります。

- 取り込む前に形式を検査し、誤りがあれば取り込みません。ファイルの文はそのまま書くので、コメントも残ります。
- 同じ名前の台本があれば、上書きするか別の名前にするかを尋ねます（上書きの前の内容は控えに残ります。下の「控え」）。
- この作業フォルダにないロボットを使う台本や、話者が未決定（TBD）の台本は、取り込んだうえで注意を出します。
- コマンドでは `talkscript import <ファイル> [--id 別の名前] [--force]` です。

画面で保存すると、台本の YAML は書き直されます（コメントは消えます）。

**控え**: 保存・取り込みの上書き・`insert --write` の前の内容は、`<名前>.yaml.<年月日-時分秒-マイクロ秒>.bak` に残します。
同じ台本・ひな形の控えは、新しい 10 個だけを残します（古いものから消します）。

**実行の記録の必須項目**: 作業フォルダの `talkscript.toml` の `[play] required_notes`（例 `["condition", "subject", "distance", "spl_db"]`）に
書いた項目が空だと、画面からもコマンドからも実行しません（空運転は除きます）。

**止まったとき**: 実行中にどんなエラーが起きても、全ロボットに停止を送り、実行の記録（`runs/<実行 ID>/run.json`）の
`result` を `error: …` にします。実行中に画面を閉じて開き直しても、「止める」ボタンと合図の欄は戻ります。

### 台本でのキーフレームの書き方

```yaml
neck_yaw:
  - {t_ms: 0, deg: 0}                                          # ブロックの始まりからの時刻
  - {at: {line: L1, mora: 2.5}, deg: 5}                        # L1 のモーラ 2 の途中（発話の中の位置）
  - {at: {line: L1, text: 紹介します, edge: end}, offset_ms: 300, deg: 0}   # 文の一部の終わり + 0.3 秒
  - {at: {line: L2, phrase: 0, edge: start}, deg: 0}           # L2 の句 0 の始まり
```

小数のモーラ番号は、モーラ i の始まりから i+1 の始まりまで（間を含む）を等分した位置です。話速を変えると、その分だけ縮みます。

形式の決まり（読み込み・合成・検査・実行の前に確かめ、違えば止めます）:

- 台本・ブロック・行・ロボット・ひな形の id（名前）は、英数字と `-` `_` だけで書きます（ファイルの名前に使うため）。
- 角度・時刻・ずらし・モーラの位置・声の数の項目・`lead_in_ms`・`gap_ms`・`tail_ms` は、有限な数で書きます
  （`.nan`・`.inf` や文字は受けません）。ロボットの側（talkscript-agent）も、受け取った軌道を同じように確かめます。

## 使い方（コマンド）

画面と同じことは、コマンドでもできます（自動化や、画面を開けない PC 向け）。

```sh
talkscript import trivia.yaml     # ほかで作った台本・ひな形を取り込む
talkscript voices                # 話者の一覧。台本の voice.speaker に話者とスタイルの名前を書く
talkscript synth demo            # 合成。句の表（番号・モーラ・開始・終了）が出る
talkscript listen demo           # 合成した音声を聞いて読みを確かめる
talkscript insert demo --block B1 --gesture nod --at "L1:text=紹介します:end" [--write]
talkscript compile demo          # 組み立てと検査
talkscript freeze demo           # 音声を固定する（purpose: experiment の台本では必須）
talkscript play demo --mode speech_motion --robots self7
```

- `play` のモード:
  - `speech_motion`: 発話と動作
  - `motion`: 動作だけ
  - `speech_hold`: 発話だけ。ロボットは休止姿勢を保持する
  - `speech_relax`: 発話だけ。ロボットにはつながない
  - `dry`: 検査だけ
- Ctrl+C を 1 回押すと、全ロボットを休止姿勢へ戻して止めます。もう 1 回押すと、すぐ止めます。
- 自作機（`self7`）の Pi 側の talkscript-agent は、このリポジトリには入っていません。ロボットの駆動部に依存するためです。
  通信の形は、PC 側の `talkscript/robots/self7.py` を見てください。agent の補間の式は、このパッケージの `talkscript.interp` を使います。
- Sota は、`config/robots/sota.yaml` の関節の向き（`sign`・`offset`）・範囲・速度の上限が TBD のままだと、検査で止まります。

## VOICEVOX の利用について

- インストーラーは VOICEVOX を再配布しません。公式の配布ファイル（GitHub Releases）を、版と sha256 を固定してダウンロードします。
- VOICEVOX の利用には、[ソフトウェアの利用規約](https://voicevox.hiroshiba.jp/term/)と、**使う声（キャラクター）ごとの利用規約**に従ってください。
  多くの声は、作品で使うときに「VOICEVOX:声の名前」のクレジットを求めています。

## 開発

```sh
uv sync --extra test             # .venv を作って依存を入れる（uv.lock の版）
uv run pytest -q                 # 擬似 VOICEVOX・擬似 RobotController を使った試験
```

`talkscript.testing` に、擬似 VOICEVOX（`FakeVoicevox`）と、一時的な作業フォルダを作る関数があります。
`tools/fake_robotcontroller.py` は、RobotController の擬似サーバです。

画面は `talkscript/gui/static/index.html`（1 つのファイル。外部の CDN は使わない）と、`talkscript/gui/server.py`（API）です。
画面の操作の試験は、ブラウザを画面なしで動かして行います（Node 22 以上）:

```sh
python tools/gui_serve_fake.py /tmp/ts-e2e 120 &     # 擬似 VOICEVOX で画面のサーバを立て、1 行目に URL を出す
node tools/gui_e2e.mjs "<その URL>" /tmp/ts-e2e shot.png
```

画面は Content-Security-Policy でスクリプトを読み込みごとの nonce に限っています。
`index.html` にインラインのイベントハンドラ（`onclick=` など）や別のスクリプトを足しても動きません。`addEventListener` を使ってください。
台本・設定から来る値を HTML に入れるときは、必ず `esc()` を通します。

アイコンの元は `talkscript/assets/icon.svg`（16〜32 px 用は `icon-small.svg`）です。直したら、画像を作り直します:

```sh
uv run --with pillow python tools/make_icons.py preview.png   # talkscript.ico・talkscript.png と見本の画像
```

版を出すとき（Windows で）:

```sh
uv build                                  # dist/ に wheel と tar.gz
python tools/build_setup.py               # dist/talkscript-setup.exe（install.ps1 の版が talkscript の版と同じか確かめる）
gh release create v<版> dist/*            # 3 つを載せる。インストーラーは talkscript-setup.exe の名前のまま載せる
```

## ライセンス

MIT（[LICENSE](LICENSE)）。VOICEVOX 本体と各声は、それぞれの利用規約に従います（上の「VOICEVOX の利用について」）。
