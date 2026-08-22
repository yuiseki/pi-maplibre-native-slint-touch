# Trident on Raspberry Pi (voice-controlled offline map)

pi4-d-hdmi 上で動く、**完全オフライン(off-grid)の音声操作地図デバイス**の実装一式。
「オーケートライデント、広島を表示して」と話しかけると、確認音声を返して地図が
広島へ flyTo する。すべて端末内で完結し、ネットワーク(pi5/クラウド)に依存しない。

これは GeoAI フラッグシップ **Trident** を Raspberry Pi 上で動かす実験の記録。
2026-06-28 時点でデモ録画まで到達。雑多だが消失防止のため退避してある。

## パイプライン

```
  マイク(Razer Seiren Mini / USB)
     │  ALSA 16kHz capture (sounddevice)
     ▼
  pi-hear (pi_hear.py)
     │  エネルギーVAD で発話区間を検出 → 別スレッドで ASR
     │  ASR エンジン差し替え可: moonshine / whisper.cpp
     ▼
  ウェイクワード照合 (wake.py)  "トライデント"
     │  katakana「トライデ」+ ローマ字 trident/tryden をファジー一致
     ▼
  地名解決 (PLACES dict)  「広島」→ hiroshima
     │  ※ 将来は llama-server(オンデバイスLLM)の意図抽出に差し替え予定
     ├──► say-muted → pi-say  「承知しました。広島を表示します。」(TTS, half-duplex)
     └──► pi-flyto hiroshima  → /dev/shm/pi-map-flyto に "lat lon zoom"
                                    │
                                    ▼
                       maplibre-slint-gl (hdmi/main_gl.cpp)
                       200ms ポーリング → smap->fly_to(lat,lon,zoom)
```

## 構成要素

| ファイル | 役割 |
|---|---|
| `pi-hear/pi_hear.py` | メイン。マイク取り込み + VAD + 転写worker + ウェイク/アクション(`--act`) |
| `pi-hear/wake.py` | ウェイクワード「トライデント」のファジー照合(katakana core + ローマ字) |
| `pi-hear/engines.py` | ASR エンジン抽象。`MoonshineEngine` / `WhisperCppEngine`(`--engine`で切替) |
| `pi-hear/pi-hear` | venv 起動ラッパー |
| `bin/pi-say` | 日本語TTS(piper-plus tsukuyomi → 3.5mmジャック)。結果WAVを (model,rate,text) でキャッシュ(`~/piper-tts/say-cache/`)。定型文「承知しました。…を表示します。」は piper 合成が A72 で **~6秒**かかるので、初回のみ合成→以降は再生のみ(~3秒=音声長)に短縮。`PI_SAY_NO_CACHE=1` で無効化 |
| `bin/say-muted` | pi-say を half-duplex 化(再生中は pi-hear をミュートして自己集音回避) |
| `bin/pi-flyto` | 地図IPC クライアント。`pi-flyto hiroshima` 等で `/dev/shm/pi-map-flyto` に書く |
| `bin/pi-net` | Wi-Fi 状態表示 / 再接続 / 時限切断(`pi-net disconnect [秒]`。復帰を切断より先に予約する) |
| `bin/pi-kbd` | Bluetooth キーボード(CardKB2)を**Pi 内蔵の無線**で掴む。接続確立の数秒だけ Wi-Fi 送信電力を絞る(下記) |
| `../hdmi/main_gl.cpp` | 地図アプリ(C++/Slint/femtovg-GL)。**flyTo IPC タイマー + render-pause** を追加済み |
| `experiments/` | エンジン比較・LLM校正・意図抽出の検証スクリプト(雑) |

## Bluetooth キーボードと Wi-Fi の共存(pi5-deck)

pi5-deck の CardKB2 は当初 USB Bluetooth ドングル経由でしか繋がらなかった。内蔵の
コントローラでは接続試行が **約99%失敗**し、`LE Connection Complete` の約340ms後に
`Connection Failed to be Established`(0x3e)で切れる。340ms は CONNECT_IND 後の
6接続イベント窓に一致する = ペリフェラルからの応答が一度も届いていない。一方でアド
バタイズは **-31dBm** で完璧に受信できており、距離もペアリングも問題ない。

**原因は感度抑圧**。CYW43455 は Wi-Fi と Bluetooth で1本のアンテナを共有しており、
Wi-Fi 送信機が隣の Bluetooth 受信段を潰す。独立アンテナを持つ USB ドングルでこれが
起きなかったのはそのため。

否定した仮説を残しておく(どちらも「効きそう」に見えるが無効だった):

- **接続間隔の不一致による LL 手続き衝突**。CardKB2 が要求する 7.5-20ms に
  `conn_{min,max}_interval` を合わせても 1.7% のまま。そもそも 340ms は LL 応答
  タイムアウト(接続イベント40回 = 1.2〜2秒)には早すぎる
- **共存アービタの設定**。Wi-Fi ファームの nvram の `btc_mode` を 0/2/1 と書き換えて
  ドライバを再読み込みしても、成功率は 1〜2% で動かない

決め手は送信電力。**Wi-Fi を接続したまま** 31dBm → 1dBm に落とすと、同じ測定が
**1.5% → 86%** に変わった。

そして**壊れるのは確立の瞬間だけ**で、一度張ったリンクは full power で 180秒 + 60秒、
切断ゼロ。だから回線を落とす必要はない。`pi-kbd` は握手に必要な数秒だけ送信機を
絞ってすぐ戻す。**ネットワークは一度も切れない**。強制切断からの再接続は 5/5 成功。

```
pi-kbd            # いまどのコントローラに繋がっているか
pi-kbd connect    # 内蔵コントローラで繋ぐ(Wi-Fi を数秒だけ絞る)
pi-kbd present    # 使える状態なら exit 0
pi-kbd watch      # 繋がり続けるように見張る(pi-kbd.service が実行)
```

設定は `/etc/default/pi-kbd`(雛形 `trident/etc/pi-kbd.default`)。AP から離れて使う
なら `PI_KBD_TXPOWER_MBM` を上げる。握手は数秒なので多少高くても構わない。

### リンクを切らさないことが最優先

**切れた瞬間ではなく、切れた後が高い**。再接続は上記の感度抑圧との戦いになるので、
一度落ちると数十秒キーが効かない。実測で 1回の切断につき **99回の再接続失敗**。

カーネル既定の監視タイムアウトは **420ms**。無操作の BLE リンクは接続間隔
(この機では 48.75ms)ごとに空パケットを交換するだけなので、420ms は**約9個分**。
隣のアンテナから Wi-Fi が一吹きすれば終わる。トレースにもそう出ている:

```
37.3s   ACL Data RX ...                      ← 通信が途絶える
97.1s   Authenticated Payload Timeout (0x57)
105.3s  Disconnect: Connection Timeout (0x08)
```

300秒の実測:

| 監視タイムアウト | 接続維持 | 切断 | 再接続試行 |
|---|---|---|---|
| 420ms (既定) | 19/40 | 3回 | 208回 |
| 6000ms | **60/60** | **0** | **0** |

**設定する場所を間違えないこと**。効くのは BlueZ のデバイス個別の値だけ。

- ✗ `/sys/kernel/debug/bluetooth/hci0/supervision_timeout` … 書けるが無視される
- ✗ `/etc/bluetooth/main.conf` の `[LE] ConnectionSupervisionTimeout` … 同上
- ✓ `/var/lib/bluetooth/<adapter>/<device>/info` の `[ConnectionParameters] Timeout=`

前2つは既定値で、**Load Connection Parameters で与えられる個別値に上書きされる**
(main.conf にもそう書いてある)。どちらを設定しても全接続が 420ms のままだった。
`pi-kbd tune` が3つ目を書き、bluetooth を再起動して読ませる(起動時にしか読まれない)。
サービスが起動時に実行するので、通常は意識しなくてよい。

### 「繋がった」の判定に使ってはいけないもの

どちらも嘘をつく。両方を組み合わせて初めて信用できる。

- **BlueZ の `Connected` プロパティ**。`LE Connection Complete` で立つ = CONNECT_IND を
  送った瞬間に立つ。ペリフェラルはまだ何も答えていない。実測で、リンク層が一度も
  確立しなかった試行の 6回中6回が `Connected: yes` を返した。しかも既定コントローラ
  の値なので、コントローラを指定できない
- **HID 入力ノードの存在**。BlueZ は再バインドのため切断後も uhid デバイスを保持する。
  `Disconnection successful` の5秒後もノードは残っていた

- **接続一覧に載っていること**だけでも足りない。切断処理中のリンクは
  `state 7`(BT_CONFIG、暗号化前)のまま一覧に残る。アドレスの一致だけを見ていた
  ため、キーが失われている最中に「繋がっている」と判定し、`pi-kbd watch` が
  再接続に介入しなかった

`pi-kbd` は「**そのコントローラに紐づくノードがある**」かつ「**カーネルの接続一覧
(`hcitool -i hciN con`)に `state 1` で5秒間途切れず載っている**」を条件にしている。失敗した試行は
340ms で崩れて即リトライされるため状態が明滅する。その明滅を落とすのが待ち時間の役割。

### キーコード

内蔵コントローラ経由では **HID usage も Linux キーコードも完全に正しい**(実測: q w e
r t y / 1〜6 / a s d f g h の18キーすべて一致。`0x14`→`KEY_Q`, `0x1e`→`KEY_1`,
`0x04`→`KEY_A`)。なお CardKB2 に **Ctrl キーは物理的に無い**。

## 端末上の配置(pi4-d-hdmi)

- `~/src/pi-hear/` … pi_hear.py / wake.py / engines.py / pi-hear
- `/usr/local/bin/` … pi-say / say-muted / pi-flyto(device-local)
- `~/.venv-ahear/` … Python 3.13 venv(moonshine_voice, sounddevice, numpy)
- `~/piper-tts/` … piper-plus arm64 バイナリ + tsukuyomi モデル(TTS)
- `~/src/whisper.cpp/` … whisper-cli/whisper-server(a72ビルド)+ ggml-{tiny,base,small}.bin
- `~/src/llama.cpp/` … llama-server/cli/bench(a72)+ Qwen2.5-{0.5B,1.5B}-Instruct gguf
- `~/maplibre-slint-gl` … 地図バイナリ(systemd: maplibre-slint-gl.service → pi-display-supervisor.py)

## 実行(デモ起動)

```bash
# pi-hear をアクションモードで(Razer Seiren Mini 用の設定)
cd ~/src/pi-hear
~/.venv-ahear/bin/python pi_hear.py \
  --engine whisper --whisper-model ~/src/whisper.cpp/models/ggml-base.bin --whisper-prompt "" \
  --device DJI --samplerate 48000 \
  --threshold 0.015 --min-speech 0.5 --act
# ↑ DJI MIC MINI 用。Razer Seiren Mini(デモ録画用)なら:
#   --device Razer --samplerate 16000 --gain 3.5 --threshold 0.08
# マイクで設定が違う(下の「マイク」節参照)。--act は ウェイク→地名→pi-say→pi-flyto まで実行。

# 地図を東京へ(待機状態)
pi-flyto tokyo
```

デモ: 待機(東京)→「オーケートライデント、広島を表示して」→ pi-say確認 → 広島へ flyTo
→「オーケートライデント、東京を表示して」→ 東京へ flyTo。

### 常駐(systemd, 再起動で自動復帰)

上の手動起動は実験用。常用は `systemd/pi-hear.service`(地図の `maplibre-slint-gl.service` と同様に boot 自動起動・`Restart=always`)。CHANGEEK USB マイクを **index でなく CARD 名**(`plughw:CARD=Device,DEV=0`)で参照し、再起動時の USB 列挙順ズレに耐える。`whisper-cli` は PATH 外なので unit の `Environment=PATH` に build dir を入れてある。

```bash
sudo install -m644 trident/systemd/pi-hear.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pi-hear.service
journalctl -u pi-hear.service -f      # WAKE / flyto / saver-ignored を確認
```

## 地図 IPC

- **flyTo**: `echo "34.385 132.455 11" > /dev/shm/pi-map-flyto`(`lat lon [zoom]`)。
  main_gl.cpp の Slint タイマーが 200ms ごとに mtime を見て `smap->fly_to()` を呼ぶ。
  起動時の既存ファイルは無視(boot で飛ばない)。
- **render-pause**: `/dev/shm/pi-map-pause` が fresh(<15s)な間、地図は**良いフレームを1枚描いてから
  描画ループの再アーム(`request_redraw`)を止める**。pi-hear の worker が ASR 中だけ touch/rm する。
  map CPU 82→0%(whisper に CPU を明け渡す)、解除すると 60ms の saver_timer(stage 0)が再アームして復帰。
  ※ `smap->render()` 自体をスキップすると V3D が一時 FBO のカラーアタッチメントを破棄して画面が灰色になる。
  だから「描画はスキップせず再アームだけ止める」=最後の良いフレームが画面に保持される。
- **screensaver-pause**: 地図が saver stage を `/dev/shm/pi-saver-stage`(0=active, 1/2/3=idle)に
  書き出し、pi-hear は `--saver-pause-stage`(既定1)以上で listening を一時停止する。
- **mic-state / caption**: pi-hear が `/dev/shm/pi-hear-state` に **1行目=状態語**
  (`listening` / `asr` / `heard` / `speaking` / `muted` / `paused`)、**2行目=字幕**を書く。
  地図はマイクアイコンの色と、**対話の段階に応じた画面表現**に使う。狙いは各段階で
  人に「いま何が起きているか」を返すこと(表現は acaption に倣った):
  - **ウェイクワード検知 → 画面を薄暗く + 波形**。「いま自分の声を聞いている」と
    「ちゃんと拾えていそうだ」を同時に返す。波形は音声レベル1つから acaption と同じ式で
    生成する(実波形ではない)。無音でも完全な直線にはしない。直線は「聞こえていない」でなく
    「UI が死んだ」に見えるため
  - **発話後の認識中 → 「考え中...」**。「伝わったから待てばよい」を返す
  - **確認音声中 → その文言**
  - **認識結果は画面に出さない**。崩れていることが多く、この画面サイズでは読めず、
    そもそも機械の都合の情報。journal には残るので追跡はできる
  **状態には優先度がある**: 取り込みループは毎秒 `listening` を書くので、素直に「最後に書いた者勝ち」に
  すると認識中は 0.5 秒で消え認識結果は一度も出ない。`hold` を持つ状態がループの更新を退ける
  (`trident/pi-hear/hear_state.py`、`trident/tests/` にテストあり)。
  色は 灰=誰も聞いていない / 緑=待ち受け / 黄=処理中。**ハートビートも兼ねる**ので、
  地図は 5 秒古いファイルを「pi-hear が動いていない」と解釈する(=idle とは別物)。
  `--state-file ""` で無効化。
- **重い表現は余裕のある時間帯に置く**。波形が動くのはウェイクワード検知後の待機中だけで、
  そこは認識器が動いていないので CPU に余裕がある。逆に**認識中は地図の描画を止めて
  whisper に CPU を渡している**ので、そこでは文字の更新だけ(毎秒4回)にしてある。
  60fps で再描画するアニメーションを認識中に走らせると、報告している当の処理から
  CPU を奪い返すことになる。
- **日本語の字幕には CJK フォントが要る**。`fonts-noto-cjk` が無いと豆腐になる
  (ステータスバーは英数字だけだったので、字幕を足すまで露見しなかった)。

## スクリーンセーバー連携(アーキテクチャ判断: タッチ起動)

アイドルが大半を占めるため、**音声起動はやらず、タッチで起こす**(誤爆が少なく低負荷)。
スクリーンセーバー中(stage>=1)は pi-hear が capture を停止し、CPU・電力・誤起動を抑える。
タッチで stage→0 になると pi-hear は即再開。声で画面を起こす配線(flyTo が last_activity を
更新)は残してあるが当面は使わない。

## ウェイク/地名のロバスト化(ローマ字 + 編集距離)— `romaji_match.py`

whisper-base は日本語の**表記**を大きく崩す(札幌→サッポロ/サッポ、沖縄→お気な、京都→…、
ウェイク トライデント→トライ弦/トライレント/トライ弁当)。が、**読み(音)は安定**している。
そこで **ASR テキストもターゲットもローマ字化(pykakasi)して、スライド窓の正規化レーベンシュタイン
距離でマッチ**する。漢字/カタカナ/ひらがなの表記ゆれを読みに畳み込み、音的崩れも吸収する。

- 例: 札幌/サッポロ/さっぽろ → 全部 `sapporo`、お気な→`okina`≈`okinawa`(距離2)、
  トライ弦→`toraigen`≈`toraidento`。
- ウェイク閾値は緩め(正規化距離≤0.45、崩れの幅が大きい)、地名は厳しめ(≤0.34、ローマ字が
  distinctive)。「今日はいい天気」「ラズベリーパイ」等は誤検出しない。
- 実測で **サッポ / トライレント のような崩れも吸収**して全コマンド成功。**個別の崩れを手で
  パッチする whack-a-mole から脱却**(initial-prompt バイアスは小モデルで幻覚を増やすので不採用)。
- 依存: `pip install pykakasi`(venv `~/.venv-ahear`)。pi_hear.py の `--act` ウェイク+地名解決は
  これを使う(旧 wake.py の仮名ファジー + 仮名辞書は置換)。**将来の llama 脳に差し替えるまでの、
  軽量で確実な中間解**。

## pi5-deck (Cortex-A76) への移設 — 2026-08-22

pi4-d-hdmi と同じ構成を [[pi5-deck]] にも用意した。**A76 は dotprod(`asimddp`)を持つ**ので、
pi4(A72)で一番苦しかった推論時間が縮む。ビルドは pi5-deck 上で直接行う(pi4 向けの
`-mcpu=cortex-a72` と cat パイプ配布は不要)。

| | pi4 (A72) | pi5-deck (A76) |
|---|---|---|
| whisper tiny | ~2 秒 | 2 秒 |
| whisper base | ~4〜5 秒 | **2 秒** |
| whisper small | ~15〜19 秒 | **8 秒** |
| piper 合成(定型文) | ~6 秒 | **0.33 秒**(RTF 0.12) |

計測は 2.35 秒の発話 1 本、`-ac 512 -bs 1 -t 4`。**pi4 では非現実的だった small が選択肢に入る**。
ただし精度比較はまだ。上の数字は piper 合成音声を入力にしており、合成音声は whisper にとって
分布外なので**認識結果の良し悪しは評価できない**(速度だけが読める)。実マイクで測り直すこと。

**Pi 5 固有の落とし穴**:

- **Bluetooth スピーカーは冒頭が切れる**。A2DP はストリームが途切れるとアンプが眠るので、
  次に鳴らすとき先頭 1 秒ほどが失われる。確認音声なら「承知しました」が消え、
  **試験で指令を流すならウェイクワードがまるごと消える**(これで認識失敗を一度誤診した)。
  `pi-say` の `PI_SAY_LEAD_MS`(この機では 1800)で**無音の頭出しをキャッシュに含める**と直る。
  この個体はデジタル無音でもアンプが起きた。起きない機種なら極小音量の音が要る
  (POI モードの先頭ビープが同じ問題への別解)。`--no-play` で鳴らさずキャッシュだけ作れる。
- **スピーカーの事情は `/etc/default/pi-say` に置く**(雛形は `trident/etc/pi-say.default`)。
  デバイス名・レート・頭出しはいずれも**スピーカーの性質**であって pi-hear の関心事ではない。
  ここに集約しておけば、**有線に替えるときはこのファイルだけ**で済み、
  `pi-hear.service` は `--say-device` すら知らずに済む。有線なら頭出しも変換も不要なので、
  `PI_SAY_DEVICE` だけ書いて残りは空にする。環境変数がファイルを、引数が両方を上書きする。
- **音声出力が無い**。Pi 5 に 3.5mm ジャックは無く、Osoyoo 3.5" パネルの HDMI は音声を拒否する
  (`aplay: audio open error: 524`)。USB マイクは capture 専用。よって `pi-say` の既定
  `plughw:0,0` は使えず、**Bluetooth (bluealsa) が唯一の出力経路**。スピーカー未接続なら
  aplay が黙って失敗するだけで、**pi-say の失敗は flyTo を止めない**(確認音声が出ないだけ)。
- **bluealsa は `-p a2dp-source` だけにする**。既定で付いてくる `a2dp-sink` / `hfp-ag` /
  `hsp-ag` は「Pi がスピーカーや通話機になる」**逆向きの用途**で、有効にすると SCO リンクが
  張られて存在しない入力が流れ続け、**スピーカーから雑音が鳴り止まなくなる**。同じ理由で
  `bluealsa-aplay.service` も disable する(BT 機器の音を Pi のローカルカードで鳴らす常駐)。
- 実行ファイル名はこのバージョンでは `bluealsad` ではなく **`bluealsa`**。override に
  `bluealsad` と書くと 203/EXEC で起動しない。
- **ALSA の `plug` に変換させると歪む**。A2DP sink は 48kHz ステレオ固定で、piper の
  22050Hz モノラルを再生時に変換すると「ビッビー」と鳴る。`defaults.pcm.rate_converter` を
  `speexrate_medium` にしても直らない=リサンプラの質ではなく**変換が課す周期**の問題。
  解は変換を再生経路から外すこと: `pi-say` に `PI_SAY_RATE=48000 PI_SAY_CHANNELS=2` を渡すと
  **sox で事前変換してキャッシュ**するので、再生は変換なしの `btspk_raw` への素通しになる。
  初回だけ変換コストを払い、以降はむしろ速い。※sox は出力の形式を拡張子で決めるので、
  キャッシュの `.tmp` 名に書くには `-t wav` が要る(無いと exit 2)。
- マイクは C-Media PCM2902 = README の表の CHANGEEK と同型。`plughw:CARD=Device,DEV=0` の
  arecord 経路がそのまま使える(card 名が `Device` なので unit を書き換えずに済む)。
- **マイクの調整は「自分で音を出して自分で録る」**。スピーカーが繋がっているなら、
  `pi-say` で喋らせて `arecord` で拾えば、人手なしで利得としきい値を実測できる。
  実測値(Adafruit 3367 mini USB mic、`Mic` 12=17.85dB、AGC off、50cm): 発話 RMS 0.267 /
  静穏部 0.009 = **29.8 倍**。既定のしきい値 0.08 のままで良い。
  ※人に話してもらう測定は、指示がブロッキングする ssh の stdout に埋もれて**相手に届かない**。
  無音を録っていることに気づかず、距離や AGC を疑って何往復も無駄にした。

## ASR / LLM エンジン比較(pi4 = Cortex-A72, dotprod 無し)での知見

- **moonshine base-ja**: ほぼ即時・自然な日本語が得意。英語略語(FOSS4G/トライデント)は崩れる。
- **whisper-tiny + `-ac 512 -bs 1`**: ~2秒。速いが日本語の崩れ多い。`-ac`(audio-ctx)必須
  (既定1500=30秒窓だと短い発話で無音から幻覚し遅い。512≈10秒で速度も精度も上がる)。
- **whisper-base + `-ac 512`**: ~4〜5秒。**地名/長文の精度が最良**(広島・トライデントを正取)。本番向き。
- **whisper-small**: ~15〜19秒。pi4 では遅すぎ。
- **whisper-server**: モデル常駐でも推論本体(30秒窓)が支配的で速くならない(llama と逆)。
- **llama-server(Qwen2.5 Q4)**: コマンド意図抽出は得意。常駐+`cache_prompt`+0.5Bで warm ~1.5〜2秒。
  cli 都度ロードは16〜20秒で不可。**崩れた ASR の修正は苦手**(base級の軽微な誤りなら可、過修正注意)。

## ハマりどころ(hard-won)

- **マイクごとに起動引数が違う(交換時は要切替)**:

  | マイク | 取り込み | 起動引数 | 備考 |
  |---|---|---|---|
  | **DJI MIC MINI**(常用) | sounddevice | `--device DJI --samplerate 48000` | 48000固定。16000不可。USB(BTは不可→#1097) |
  | **Razer Seiren Mini** | sounddevice | `--device Razer --samplerate 16000 --gain 3.5` | 44.1kネイティブだが PortAudio で16k開ける |
  | **CHANGEEK Mini USB**(常用) | arecord | `--alsa-device plughw:3,0 --samplerate 16000` | TI PCM2902, 44.1k固定・16k不可。gain不要 |

  - **DJI は 16000 で開けない**: `PortAudioError: Invalid sample rate [PaErrorCode -9997]`。
    48000 で取り込み、`engines.to_16k` の 48k→16k=3:1 線形補間(=実質デシメーション)で綺麗に
    16k化される(元から良好)。gain 補償も不要。
  - **Razer は逆の罠2つ**: (1) PortAudio が arecord(plughw)の約1/3のレベルで取り込む
    → `--gain 3.5` で補償。(2) 44100→16000=非整数比の線形補間リサンプルはエイリアシングで
    音を歪ませ whisper が `(笑)(音楽)` と誤認 → **`--samplerate 16000` で直接取り込み**、
    ALSA(plughw)に適切なアンチエイリアス変換をさせる(Razer は 16000 を開ける)。
  - **PortAudio で 16000 を開けない USB マイク(44.1k/48k固定。DJI・CHANGEEK 等)は、pi-hear の
    arecord バックエンドで `--alsa-device plughw:CARD,0 --samplerate 16000` を使う**のが正解。
    ALSA plug が 44100→16000 を綺麗にリサンプル(PortAudio の16k不可 と pi-hear の np.interp
    線形補間の歪み=Razerで`(笑)(音楽)`化、の両方を回避)。CHANGEEK(PCM2902)はこれで崩れ無し・
    gain不要(発話 peak~0.15)。Razer は PortAudio で16k開けたので sounddevice+gain3.5 だった。
    card番号が動くなら `plughw:CARD=Device,DEV=0`。
  - 切り分け検証: `arecord -D plughw:3,0 -f S16_LE -r 16000 -c1 -d 10 x.wav` で直接録音し
    `whisper-cli` にかけると、pi-hear 経路と分離してマイク単体の素性が分かる。
  - 教訓: マイクを替えただけで、ネイティブ rate / PortAudio のレベル / リサンプル品質が変わり、
    認識が壊滅する。**rate と gain は実機で必ず再調整**(`--debug` の onset/flush peak を見る)。
- **whisper は負荷時トライデントをローマ字化**(OKTryDent)→ wake.py に `ROMAJI_CORES`。
- **雑音で whisper 暴走**: gain は雑音も増幅。`--threshold 0.08 --min-speech 0.5` で近接発話だけ発火。
- **自己集音**: pi-say の音をマイクが拾う → half-duplex(say-muted が `/tmp/pi-hear/mute` を touch)。
  転写が遅い whisper では**転写を別スレッド化**しないとキャプチャが詰まって TTS が漏れる。
- **ウェイクと地名の発話分割**: 「トライデント、(間)、札幌を表示して」と区切ると VAD が2つの発話に
  割り、`OK トライデント`(地名なし)と `サッポの表示して`(ウェイクなし)で**両方とも失敗**する。
  対策=`act()` を**アーム式**に: ウェイク検知で `ARM_WINDOW`(既定8s)アーム→続く発話の地名を受理。
  一息で言えた場合(`OKTryDenと広島を表示して`)は従来どおり即実行。
- **ビルド/配布**: ビルドは pi5(A76)で `-mcpu=cortex-a72`(pi4 は dotprod 無し、native だと
  illegal instruction)。pi5→pi4 配布は cat パイプ+md5+原子mv(scp/背景実行は Text file busy で破損)。

## POI モード(`pi-say-poi`) — The Machine 風の継ぎ接ぎ音声

Person of Interest の「The Machine」オマージュ。文を**単語ごとに別の英語音声**で喋らせ、
先頭にビープを付けて継ぎ接ぎする。`bin/pi-say-poi` + `pi-hear/poi_say.py`。

```bash
pi-say-poi "can you hear me?"                                  # ベスト既定値
pi-say-poi --rate 1.25 --gap 0.14 --beep_s 0.5 --beep_hz 1000 "can you hear me?"
```

- **単語→声**はハッシュで決定(同じ単語は常に同じ声=安定スニペット)。声は en_US-ryan/amy,
  en_GB-alan/southern_english_female の low モデル4種。
- **キャッシュ**: 単語クリップを `(声, rate, 単語)` 単位で `~/piper-tts/poi-cache/` に保存。
  piper 合成は初回のみ、以降は連結だけ(~0.3秒)。これが The Machine の本来の仕組み(録音済み
  単語の継ぎ接ぎ)。
- **先頭ビープ**は signature であると同時に **Bluetooth SCO のウェイクアップ**(先頭単語の頭切れ防止)。
- **要らなかった寄り道**: 「文を喋らせて単語境界で分割」は、エネルギー分割も whisper の
  単語タイムスタンプ(短クリップで縮退)も不安定で断念。**単語ごと孤立合成+キャッシュが確実**。
- 前提: 英語モデルを `piper --download-model en_US-ryan-low --model-dir ~/piper-tts/en-models`
  等で4種取得。出力は **`--play`(既定 btspk)→ 失敗時 `--fallback`(既定 plughw:0,0=3.5mm)**
  へ自動フォールバック(BT未接続なら aplay が即 "No such device" で落ち→3.5mmで再生)。
  3.5mm はジャック検出が無く常に受理する終端フォールバック。

## Bluetooth オーディオ(Aeropex / bluealsa)

PulseAudio 無しの構成で BT ヘッドセット(AfterShokz Aeropex、HFP/SCO 16kHz mSBC)を使う:

- `sudo apt install bluez-alsa-utils libasound2-plugin-bluez`。bluealsa は **HFP-AG を有効化**
  (`/etc/systemd/system/bluealsa.service.d/override.conf` で `-p a2dp-source -p a2dp-sink
  -p hfp-ag -p hsp-ag`)。Pi が Audio Gateway 側。
- `~/.asoundrc` に plug 付き名前付き PCM `btspk`/`btmic`(`type plug` → `type bluealsa,
  device "20:74:CF:D2:A3:84", profile "sco"`)。**インライン `plug:bluealsa:DEV=...` 構文は不可**。
- **マイク**: PortAudio(sounddevice)は bluealsa PCM を列挙しない → pi-hear は
  `--alsa-device "bluealsa:DEV=...,PROFILE=sco" --samplerate 16000`(arecord 経路)で取り込む。
  **装着(口元)必須**。机置きだと拾えない。
- **スピーカー**: `--say-device btspk`。SCO 再生は立ち上がりに頭切れ → pi-hear の arecord が SCO を
  温め続けるので解消(+ POI ビープも保険)。22050→16000 のリサンプルは `plug` が担当。
- HFP 音量レンジは **0〜15**(0〜100 ではない)。

## ライセンス注意

moonshine モデルは **Moonshine Community License(非商用)**。製品化時は要確認。
