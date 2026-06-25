# Dance のカクつき徹底調査

pi4-d-hdmi（Pi4 + HDMI、V3D GPU、Slint 零コピーGL、内部描画 720x480）で "Dance"
（pitch+bearing を連続スイープする演出）中に FPS が落ちる現象の根本原因と対策を、
仮説検証で潰した記録。計測は全て `MAPLIBRE_PERF=1` の `[perf]` ログ（fps / frame /
runloop=タイル処理 / render=V3D GPU+配置 / slow>33ms）による。

初期視点は `slint_map_gl.cpp` で **Tokyo (35.681,139.767) z10** に jumpTo（既定）。
`MAPLIBRE_CENTER=lat,lon,zoom` env（本調査で追加）で変更可。

## 確定事実（前段の調査より）

- **F1**: Dance のコストは **bearing 回転が支配的**。pitch=0 に固定した「回転のみ」実験で
  フル Dance とほぼ同じだけ FPS が落ちる（z16: 回転のみ ~20fps ≈ フル ~20fps / z10:
  回転のみ ~50fps ≈ フル ~45fps）。**pitch はほぼ無関係**。
- **F2**: `runloop`（タイル取得/処理）は定常 ~0.01ms。タイル読込律速ではない。コストは
  `render`（GPU描画＋シンボル配置）に集中。
- **F3**: feature/ラベル密度に比例。osm-bright(render ~11ms) > maptiler-basic(~6ms)。
- **F4**: 高ズームほど激落ち。z10 ~13ms/50fps、z14 ~50ms/15fps、z16 ~40ms/20fps。
- **F5**: 解像度レバーは死亡。HDMIブリッジが 480x320 を拒否（No Signal）、640x480 は誤差。
- **F6**: `DANCE_SPEED`↓ は床を上げない（体感のみ）。`DANCE_MAX_PITCH`↓ も無効（pitchが犯人でないため）。
- **F7**: vsync 量子化。render+compositing が 16.7ms を超えると次 vsync まで待ち 33ms(30fps)へ。
- **仮説（主犯）**: bearing 変化のたび MapLibre が**シンボル/ラベルの再配置（衝突判定・上向き維持,
  placementChanged）を毎フレーム再計算**しているのが回転コストの正体。osm-bright は symbol 28層。

## 検証する仮説

- **H1**: symbol 層を全部消すと回転が軽くなる（配置コストが主犯の確認）
- **H2**: `text/icon-allow-overlap=true`（衝突判定スキップ）で配置が軽くなる
- **H3**: symbol 層を減らす/簡素化した dance 専用スタイル osm-bright-dance で見た目を保ちつつ高速化
- **H4**: pitch+bearing を毎フレーム jumpTo するアプリ側の駆動が placement を毎回起こす。
  upstream/アプリ側で配置の頻度を落とす/回転中は配置を凍結できないか
- **H5**: ラベルの fade/transition、symbol-sort、collision を切る各種 layout/paint で改善するか
- **H6**: その他（line 77層の寄与、fill、アンチエイリアス、tile over-zoom 等）

---

## ログ
（以下、各実験の結果を時系列で追記）

### 2026-06-25 実験1: H1/H2 — 回転のみ(pitch=0) z16 Tokyo, osm-bright系

| スタイル | render avg | fps | 判定 |
|---|---|---|---|
| osm-bright（基準） | ~45–55ms | 17–20 | — |
| osm-bright-nosymbol（symbol28層除去, 128→100層） | ~6–10ms | **49–60** | **H1確定** |
| osm-bright-allowoverlap（text/icon-allow-overlap+ignore-placement） | ~52–57ms | 16–17 | **H2否定** |

**結論**: 回転コストのほぼ全ては **symbol(ラベル/アイコン)層の描画**。symbol を消すと render が
50ms→7ms に激減し z16 でも ~56fps。一方 **衝突判定スキップ(allow-overlap)は無効**＝犯人は
placement/collision の計算ではなく、**回転に伴うシンボル描画（viewport整列グリフの毎フレーム
頂点再構築/再アップロード/再描画）そのもの**。`runloop` は全条件 ~0.01ms でタイル無関係を再確認。

→ 次: ラベルを残したまま速くできるか。H7 として symbol を `*-rotation-alignment: map`/
`*-pitch-alignment: map`（ラベルをマップと一緒に剛体回転＝viewport再整列を避ける）を検証。

### 2026-06-25 実験2: H7 — rotation-alignment:map（回転のみ z16）
osm-bright-rotmap: render ~48ms / **19fps**。**改善せず（H7否定）**。

**決定的な切り分け**: 同じラベル数でも
- 静止（各runの開始 bearing0/pitch0）: render **~2ms / 60fps**
- 回転中: render **~50ms / 19fps**

GPU が描くグリフ数は静止も回転も同じなのに 48ms 増える → コストは **GPUのグリフ描画ではなく、
カメラ変化のたび毎フレーム走る CPU 側の symbol placement（投影・レイアウト再計算）**。
collision スキップ(allow-overlap)も整列変更(rotmap)も無効だったのは、犯人が衝突判定でも
viewport整列でもなく「placement パスが毎フレーム全シンボルを処理すること」自体だから。

**含意**: MapLibre GL JS は placement を毎フレームでなく約300ms間隔に throttle している。
maplibre-native が回転中に毎フレーム placement している場合、**upstream で throttle すれば
「全ラベルを保ったまま」回転を軽くできる**（最有力の本質的修正）。→ H8 として mbgl の
placement 頻度を調査・改修。並行して実用デリバラブル osm-bright-dance（symbol削減）も用意。

### 2026-06-25 実験3: mbgl コード解析（毎フレームコストの所在）
- `RenderTreeImpl::prepare()`（**毎フレーム実行**）→ `placement->updateLayerBuckets()` →
  `bucket.updateVertices()` → `updateBucketDynamicVertices()`。
- `symbol-placement: line` のラベル（道路名等, rotation-alignment 既定=map）は、ここで
  **毎フレーム `reprojectLineLabels()`（線に沿った各グリフの再投影）** を実行。これが回転コストの中核。
- placement 本体（`placeLayers`=衝突判定）は `placementController.placementIsRecent()` で
  ~300ms 間隔に throttle 済み。つまり**衝突判定は毎フレームではない**（だから allow-overlap が無効だった）。
  per-frame コストは reprojection と POI の頂点/不透明度更新。
- osm-bright の line配置symbol: waterway-name, water-name-lakeline, road_oneway(+opposite),
  highway-name-path/minor/major（計7層）。

### 2026-06-25 実験4: ラベル削減レベル別（回転のみ z16, 最悪条件）

| スタイル | 残すラベル | render avg | fps |
|---|---|---|---|
| osm-bright（基準） | 全部 | ~50ms | 17–20 |
| osm-bright-dance v1（line除去のみ） | 地名+POI | ~30ms | 28–30 |
| **osm-bright-dance v2（line+POI除去）** | **地名/シールド/水域/空港** | **~8.6ms** | **59.7 ロック** |
| osm-bright-placeonly（地名のみ） | 地名のみ | ~6.9ms | 59.7 ロック |

**結論**: 道路名(line reproject)＋POI(高ズームで膨大)を落とすと、**地名ラベルを残したまま
z16では 60fps 完全ロック**。

### 2026-06-25 実験5: z10(Dance既定視点)フルDance — ラベル削減レベル別

| スタイル | z10 render | z10 fps |
|---|---|---|
| osm-bright（基準） | ~14–19ms | 37–48（カクつく） |
| osm-bright-dance(line+POI除去・地名維持) | ~15–21ms | 36–42 |
| osm-bright-placeonly(地名のみ) | ~11–13ms | 47–58 |
| **osm-bright-nosymbol(ラベル全除去)** | **~9ms** | **52–60 ロック** |

**重要**: z10(広域)は可視の地名ラベルが多く point ラベルも高コスト。**全ズームで Dance を 60fps
ロックするにはラベル全除去が必要**。Dance は回転する eye-candy なのでラベル無しは見栄え上も自然。

## ★ 解決策（実装・検証済み）

**Dance 用スタイル `osm-bright-dance.json`（= osm-bright から symbol 28層を全除去、128→100層）**
を pi5 `/data/static/styles/` に配置。全ズーム・フルDance で **60fps 完全ロック**（render ~9ms）。

**アプリ統合**（`slint_map_gl.cpp`）: `set_dance(true)` で現スタイルを退避し
`MAPLIBRE_DANCE_STYLE_URL`（既定 osm-bright-dance）へ自動切替、`set_dance(false)` で復帰。
camera(center/zoom)は load 跨ぎで保持。env `MAPLIBRE_ORIENTATION_DEMO=1` 起動経路も同じ swap を通す。
`MAPLIBRE_DANCE_STYLE_URL=""` で無効化（単体計測用）。

**end-to-end 検証**: ORIENTATION_DEMO=1（既定osm-bright, z10）→ 自動で osm-bright-dance へ swap、
**~58–60fps ロック**（修正前 37–48fps）。通常時は osm-bright 表示・回帰なし。

### 副産物の env レバー（本調査で追加, 既定は従来動作）
- `MAPLIBRE_CENTER=lat,lon,zoom` 起動視点（既定 Tokyo z10）。boot-to-location に有用。
- `MAPLIBRE_DANCE_MAX_PITCH`（既定45）pitch上限。**perfには無効**（pitchは犯人でない）が無害なノブ。
- `MAPLIBRE_DANCE_STYLE_URL`（既定 osm-bright-dance）Dance時スタイル。

### 別解・将来
- **placeonly**（地名のみ残す, `/data/static/styles/osm-bright-placeonly.json`）: z16=60ロック,
  z10~50fps。地名を残したい場合の選択肢（`MAPLIBRE_DANCE_STYLE_URL` で切替可）。
- **upstream throttle**: `RenderTreeImpl::prepare()` の毎フレーム `reprojectLineLabels` を回転中
  throttle すれば line ラベルは救えるが、z10 の point ラベルコストは残るため**全ズーム60ロックには
  不足**。スタイル側の解決（symbol除去）が確実で副作用も少ない。採用見送り。
