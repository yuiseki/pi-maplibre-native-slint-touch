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
| `bin/pi-say` | 日本語TTS(piper-plus tsukuyomi → 3.5mmジャック) |
| `bin/say-muted` | pi-say を half-duplex 化(再生中は pi-hear をミュートして自己集音回避) |
| `bin/pi-flyto` | 地図IPC クライアント。`pi-flyto hiroshima` 等で `/dev/shm/pi-map-flyto` に書く |
| `../hdmi/main_gl.cpp` | 地図アプリ(C++/Slint/femtovg-GL)。**flyTo IPC タイマー + render-pause** を追加済み |
| `experiments/` | エンジン比較・LLM校正・意図抽出の検証スクリプト(雑) |

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
