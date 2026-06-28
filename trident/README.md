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
  --device Razer --samplerate 16000 --gain 3.5 \
  --threshold 0.08 --min-speech 0.5 --act

# 地図を東京へ(待機状態)
pi-flyto tokyo
```

デモ: 待機(東京)→「オーケートライデント、広島を表示して」→ pi-say確認 → 広島へ flyTo
→「オーケートライデント、東京を表示して」→ 東京へ flyTo。

## 地図 IPC

- **flyTo**: `echo "34.385 132.455 11" > /dev/shm/pi-map-flyto`(`lat lon [zoom]`)。
  main_gl.cpp の Slint タイマーが 200ms ごとに mtime を見て `smap->fly_to()` を呼ぶ。
  起動時の既存ファイルは無視(boot で飛ばない)。
- **render-pause**: `/dev/shm/pi-map-pause` が fresh(<15s)な間、地図は `smap->render()` を
  スキップ。pi-hear の worker が ASR 中だけ touch/rm する。map CPU 77→38%、whisper ~24%速。

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

- **マイク交換は地獄**: DJI(48kHz)→ Razer(44.1kHz, 低レベル)で大ハマり。
  (1) PortAudio は arecord(plughw)の約1/3のレベルで取り込む → `--gain 3.5` で補償。
  (2) 線形補間リサンプル(44100→16000=非整数比)はエイリアシングで音を歪ませる
      → **`--samplerate 16000` で直接取り込み**、ALSA に適切に変換させる。
  検証: `arecord -D plughw:3,0 -f S16_LE -r16000 -c1 -d10 x.wav` → whisper-cli で素性確認。
- **whisper は負荷時トライデントをローマ字化**(OKTryDent)→ wake.py に `ROMAJI_CORES`。
- **雑音で whisper 暴走**: gain は雑音も増幅。`--threshold 0.08 --min-speech 0.5` で近接発話だけ発火。
- **自己集音**: pi-say の音をマイクが拾う → half-duplex(say-muted が `/tmp/pi-hear/mute` を touch)。
  転写が遅い whisper では**転写を別スレッド化**しないとキャプチャが詰まって TTS が漏れる。
- **ビルド/配布**: ビルドは pi5(A76)で `-mcpu=cortex-a72`(pi4 は dotprod 無し、native だと
  illegal instruction)。pi5→pi4 配布は cat パイプ+md5+原子mv(scp/背景実行は Text file busy で破損)。

## ライセンス注意

moonshine モデルは **Moonshine Community License(非商用)**。製品化時は要確認。
