# Julius wake grammar

The deck's wake word, as a Julius grammar. `pi-hear` runs this in front of
whisper: while disarmed, an utterance is only worth a whisper call if Julius
heard the wake word in it.

## Why not just match whisper's text

whisper writes the wake word down as whatever it sounded like. On 92
utterances this deck recorded on 2026-08-23, all of them attempts at the wake
word or commands after it:

| method | wake words found (of 42) | false alarms | RTF |
|---|---|---|---|
| this grammar | 36 (86%) | 0 | 0.107 |
| wake word in whisper's text | 26 (62%) | 0 | 0.35 |

The 16 that only Julius found were written by whisper as `OK トライドエンド`,
`OK トライダーエント`, `OK トライダイム`, `OK、とりあえず`. The sound was the
wake word; the text was not. All 22 utterances the two methods disagreed
about were played back and confirmed by ear: every one was the wake word.

## Why the garbage loop

`GLOOP` is a loop over every phone in the acoustic model. Without it the
grammar has nothing to say except the wake word, so it answers "wake word" to
any audio at all. With it, a detection means the wake word fitted the audio
better than arbitrary phones did. Over 31 speech segments cut out of 18
minutes of radio news, nothing fired.

`sp` is deliberately not in the loop: a skippable phone that can repeat makes
mkdfa fail with `mkcpair: skippable sp should not repeat`.

## Building it

Needs the Japanese acoustic model from
[grammar-kit](https://github.com/julius-speech/grammar-kit)
(`model/phone_m/hmmdefs_ptm_gid.binhmm` and `logicalTri`).

```sh
mkdfa.pl wake            # -> wake.dfa wake.dict wake.term
```

Recursion goes left (`GLOOP: GLOOP G`). mkfa reverses the grammar before
compiling, so the right-recursive spelling is rejected as left recursion.

Install the four files plus the two model files somewhere stable and point
`PI_HEAR_JULIUS_*` at them; `--julius-dfa` is what turns the gate on.

## Building Julius on aarch64

Julius 4.6 does not compile on a Raspberry Pi as it stands.

- `config.guess` predates aarch64: pass `--build=aarch64-unknown-linux-gnu`.
- `libsent/src/phmm/calc_dnn.c` includes `omp.h` inside a `#if defined(HAS_SIMD_*)`
  block, but uses `omp_get_max_threads()` and `omp_get_thread_num()` under
  `#ifdef _OPENMP` alone. No SIMD macro is set on aarch64 (the shipped
  `configure` has no NEON test at all), so the build fails. Move the include
  out of the SIMD block.
