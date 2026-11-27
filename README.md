# talkscript 作業フォルダ（Sota の動作確認, 2026-11-27）

このブランチ（`sota-results`）は、talkscript の作業フォルダです。台本・ロボットの設定・実行の記録が入っています。
ツール本体は `main` ブランチにあります。

## 中身

| 場所 | 内容 |
|---|---|
| `talkscript.toml` | 作業フォルダの設定。ロボットのアドレスは公開しないため `TBD` にしてある。PC ごとの `settings.toml` の `[sota] host`・`[self7] host` に書く |
| `config/robots/sota.yaml` | Sota の関節の向き（sign・offset）・範囲・速度の上限・遅れ（latency_s = 0.19 s）。実機で確かめた方法はコメントに書いた |
| `config/gestures/` | うなずき（nod）・右を指す（point_right）のひな形。Sota 用の角度を追加した |
| `scripts/demo.yaml` | デモ台本。話者はずんだもん（ノーマル）。self7 と Sota の両方の動作がある |
| `scripts/trivia_octopus.yaml` | 台本（self7） |
| `build/` | 組み立ての出力（軌道・発話の位置）。合成した音声（.wav）は入れていない。`talkscript synth <台本>` で作り直せる |
| `runs/` | 実行の記録。`summary.md` に要約がある |

## Sota の測定（2026-11-27）

- 1 回の指令（play_pose）を送ってから動き出すまで: 中央値 196 ms（15 回、範囲 132〜311 ms）
- 姿勢の列（play_motion）では、遅れは 0.2〜0.28 s。列のどの位置でもほぼ同じで、積み重ならない
- 台本の再生中の実際の角度（`runs/20261127_163258_demo/sota_axes.csv`、`runs/20261127_163406_demo/sota_axes.csv`）:
  動き出しは予定から +0.12〜0.45 s。うなずき・指さしの頂点は予定とほぼ同じ時刻
- 実際の音の出力時刻は、マイクが使えず測れていない

## 使い方

```sh
talkscript play demo --robots sota   # Sota だけで再生する（Sota で RobotController を起動しておく）
```

合成した音声を作品で使うときは、クレジット「VOICEVOX:ずんだもん」を書く。
