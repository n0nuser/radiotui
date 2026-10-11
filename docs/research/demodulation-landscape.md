# Research: demodulation landscape and what radiotui should adopt

Issue: [#77](https://github.com/n0nuser/radiotui/issues/77).
Period: 2026-10-12.
Related decisions: [ADR-0002](../adr/0002-numpy-only-receive-chain.md),
[ADR-0003](../adr/0003-channel-filter-decimation.md),
[ADR-0009](../adr/0009-regional-radio-settings.md),
[ADR-0010](../adr/0010-classifier-vox-gate.md).
Follows [the audio-chain study](audio-chain-redesign.md).

Conventions: "(P)" = primary source read in this study (spec text or source
file). "(S)" = secondary source only (wiki, forum, vendor page); treat as a
lead. "(E)" = my engineering estimate, not measured. Effort and CPU figures
are all (E) unless a benchmark is cited.

## What radiotui does today

Read from `src/radiotui/audio/demod.py` and `core/models.py`:

- Modes: `NFM`, `WFM`, `AM` only (`DemodMode`).
- FM: conjugate-product + `np.angle` (polar discriminator), clipped to
  +-1.5 (`demod_nfm`). WFM adds a 50/75 us single-pole de-emphasis as a
  truncated FIR (ADR-0002, ADR-0009).
- AM: envelope, ~4 ms moving-average DC removal, slow block AGC.
- Channel filter: Hamming-windowed sinc evaluated polyphase (ADR-0013,
  which superseded ADR-0003's triangular kernel): about -66 dB at 200 kHz
  for WFM. WFM also low-passes to 15 kHz before resampling to 48 kHz, so
  the pilot and stereo subcarriers no longer fold into the audio.
- Squelch: RF level gate (ADR-0008) and an audio "is this speech" VOX gate
  (`classify.py`, ADR-0010). No noise squelch, no tone squelch.
- No sub-audible high-pass in NFM: the discriminator output keeps 67-254 Hz
  CTCSS and 134.4 bps DCS energy in the audio the user hears and records.
- Ham HF bands `hf_ham_80m` and `hf_ham_40m` demodulate as AM (`config.py`
  `BANDS`), but voice on those bands is single sideband, so it is not
  intelligible. This is the largest functional gap against every other SDR
  app below.
- Default sample rate 1.024 MS/s (`ScannerSettings`).

## Reference implementations and licences

radiotui is MIT ([LICENSE](../../LICENSE)). GPL code cannot be copied or
mechanically translated into it. Permissive references are the safe place to
read an algorithm; for everything else implement from the published spec.
Licences below are from each repo's own LICENSE/README/header, fetched
2026-10-12.

| Project | Licence | Evidence |
| --- | --- | --- |
| rtl-sdr (`rtl_fm`) | GPL-2.0+ | [header of rtl_fm.c](https://github.com/osmocom/rtl-sdr/blob/master/src/rtl_fm.c) |
| GNU Radio | GPL-3.0+ | [ctcss_squelch_ff_impl.cc SPDX line](https://github.com/gnuradio/gnuradio/blob/main/gr-analog/lib/ctcss_squelch_ff_impl.cc) |
| SDR++ | GPL-3.0 | [repo](https://github.com/AlexandreRouma/SDRPlusPlus) |
| GQRX | GPL-3.0+ | [rx_noise_blanker_cc.cpp header](https://github.com/gqrx-sdr/gqrx/blob/master/src/dsp/rx_noise_blanker_cc.cpp) |
| SDR# | closed source (S) | not verified, no public repo |
| multimon-ng | GPL-2.0 | [repo](https://github.com/EliasOenal/multimon-ng) |
| Dire Wolf | GPL-2.0+ | [demod_afsk.c header](https://github.com/wb2osz/direwolf/blob/master/src/demod_afsk.c) |
| dump1090 (antirez) | BSD-3-Clause | [README](https://github.com/antirez/dump1090/blob/master/README.md) |
| dump1090 (FlightAware) | GPL-2.0+ | [LICENSE](https://github.com/flightaware/dump1090/blob/master/LICENSE) |
| readsb | GPL-3.0+ | [LICENSE](https://github.com/wiedehopf/readsb/blob/dev/LICENSE) |
| AIS-catcher | GPL-3.0 | [README](https://github.com/jvde-github/AIS-catcher) |
| acarsdec (f00b4r0 fork) | GPL-2.0-only | [README](https://github.com/f00b4r0/acarsdec) |
| redsea | ISC-style permissive (GitHub reports MIT) | [channel.cc header](https://github.com/windytan/redsea/blob/master/src/channel.cc) |
| DSD (szechyjs) | ISC | [README](https://github.com/szechyjs/dsd) |
| DSD-FME | mixed: ISC base plus GPL-2/GPL-3 borrowed parts | [README](https://github.com/lwvmobile/dsd-fme/blob/audio_work/README.md), [COPYRIGHT](https://github.com/lwvmobile/dsd-fme/blob/audio_work/COPYRIGHT) |
| OP25 (boatbod) | GPL-3.0+ | [p25p2_tdma.cc header](https://github.com/boatbod/op25/blob/master/op25/gr-op25_repeater/lib/p25p2_tdma.cc) |
| SDRTrunk | GPL-3.0 | [repo](https://github.com/DSheirer/sdrtrunk) |
| mbelib | ISC code, patent notice | [COPYRIGHT](https://github.com/szechyjs/mbelib/blob/master/COPYRIGHT), [README](https://github.com/szechyjs/mbelib/blob/master/README.md) |
| Codec2 | LGPL-2.1 | [repo](https://github.com/drowe67/codec2) |

Permissive references worth reading: antirez dump1090 (ADS-B), redsea (RDS),
DSD/mbelib (digital voice framing, ISC). Everything else is GPL.

## Analog voice

### FM discriminator

- Polar / conjugate product: `atan2(Im(x[n] conj x[n-1]), Re(...))`. This is
  what `rtl_fm` does in `polar_discriminant()`, with `fast_atan2` and a LUT
  as speed variants ([rtl_fm.c](https://github.com/osmocom/rtl-sdr/blob/master/src/rtl_fm.c), P).
  radiotui's `demod_nfm` is the same algorithm. Nothing to change.
- GNU Radio `quadrature_demod_cf` is the same conjugate-product-then-arg
  form with a gain; there is also `fmdet_cf` and PLL-based `pll_freqdet_cf`
  ([block list](https://github.com/gnuradio/gnuradio/tree/main/gr-analog/include/gnuradio/analog), P).
  A PLL discriminator buys threshold extension at low SNR; a per-sample PLL is
  a Python loop in NumPy, so not worth it.
- SDR++ also ships an "FM IF noise reduction" block that keeps only the
  strongest FFT bin per sample ([fm_if.h](https://github.com/AlexandreRouma/SDRPlusPlus/blob/master/core/src/dsp/noise_reduction/fm_if.h), P). It is a per-sample FFT; infeasible in NumPy at these rates.
- Feasibility: already done. CPU trivial.

### De-emphasis and voice shaping

- Broadcast: ITU-R BS.450-3 gives pre-emphasis 50 us (Europe) or 75 us (US),
  max deviation 75 kHz ([BS.450-3 section 1](https://itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.450-3-200111-S!!PDF-E.pdf), P). Already in ADR-0009.
- `rtl_fm` hard-codes 75 us on its de-emphasis path ([rtl_fm.c `deemph_filter`](https://github.com/osmocom/rtl-sdr/blob/master/src/rtl_fm.c), P); GQRX takes tau as a parameter
  (0 disables) ([rx_demod_fm.h](https://github.com/gqrx-sdr/gqrx/blob/master/src/dsp/rx_demod_fm.h), P).
- Land-mobile NFM: radios high-pass at ~300 Hz (to drop CTCSS/DCS) and
  low-pass at ~3 kHz. TIA-603 is the governing standard and is paywalled, so
  I could not read its receiver clauses (S: [Wikipedia CTCSS](https://en.wikipedia.org/wiki/Continuous_Tone-Coded_Squelch_System)).
  The "750 us" figure in the brief is unverified and I found no source for it.
- Feasibility: a 300 Hz high-pass at the ~30 kS/s NFM channel rate is a
  single `np.convolve` with a windowed-sinc kernel (about 200 taps) and carried
  history, the same pattern as `deemphasize_fir`. A single-pole HPF
  (input minus the existing truncated-FIR low-pass) is cheaper but only
  gives ~12 dB at 100 Hz; a longer FIR does better. Under ADR-0002.

### AGC and noise blanker

- GNU Radio: `agc_*`, `agc2_*`, `feedforward_agc_cc`, `rail_ff`
  ([list](https://github.com/gnuradio/gnuradio/tree/main/gr-analog/include/gnuradio/analog), P). SDR++ uses attack/decay AGC in SSB and CW ([ssb.h](https://github.com/AlexandreRouma/SDRPlusPlus/blob/master/core/src/dsp/demod/ssb.h), P).
- SDR++ noise blanker: running mean of |x|; if |x|/mean exceeds a level,
  divide the sample by the excess ([noise_blanker.h](https://github.com/AlexandreRouma/SDRPlusPlus/blob/master/core/src/dsp/noise_reduction/noise_blanker.h), P).
  GQRX has a comparable `rx_noise_blanker_cc` ([source](https://github.com/gqrx-sdr/gqrx/blob/master/src/dsp/rx_noise_blanker_cc.cpp), P).
- Feasibility: the blanker is vectorisable in NumPy (block mean of |x| or an
  exponentially weighted version via the truncated-FIR trick, then
  `np.where(excess > level, x/excess, x)`). It must run on IQ before channel
  decimation, so ~1 MS/s of complex math per block: cheap (E). Useful for
  ignition/electric-fence pulses on HF and low VHF. radiotui already has a
  block AGC for AM.

### AM: envelope vs synchronous

- Envelope: `rtl_fm am_demod` is sqrt(I^2+Q^2) ([rtl_fm.c](https://github.com/osmocom/rtl-sdr/blob/master/src/rtl_fm.c), P); radiotui matches it plus DC removal and AGC.
- Synchronous AM: GQRX `rx_demod_amsync` runs a carrier-tracking PLL
  (`pll_carriertracking_cc`) and takes the real part, with DC removal
  ([rx_demod_am.h](https://github.com/gqrx-sdr/gqrx/blob/master/src/dsp/rx_demod_am.h), P). It helps against selective fading on shortwave.
- Feasibility: a PLL is a per-sample recurrence. A block-wise open-loop
  alternative (estimate carrier frequency/phase per block from the
  low-passed carrier, derotate, take the real part) is NumPy-friendly but
  weaker on fast fading. Low priority.

### SSB (USB/LSB) and CW

- `rtl_fm` fakes it: `usb_demod` outputs I+Q, `lsb_demod` I-Q ([rtl_fm.c](https://github.com/osmocom/rtl-sdr/blob/master/src/rtl_fm.c), P),
  which does not reject the opposite sideband without prior filtering.
- SDR++ SSB: translate the channel by +-bandwidth/2, low-pass, take the real
  part, then AGC ([ssb.h](https://github.com/AlexandreRouma/SDRPlusPlus/blob/master/core/src/dsp/demod/ssb.h), P). CW: translate by the beat tone, real part, AGC ([cw.h](https://github.com/AlexandreRouma/SDRPlusPlus/blob/master/core/src/dsp/demod/cw.h), P).
- Feasibility: very high. `frequency_shift()` exists in `demod.py`; the
  channel kernel is already applied before. Add `DemodMode.USB`, `LSB`, `CW`,
  shift, low-pass to ~2.4 kHz (SSB) or ~500 Hz (CW), `.real`, slow AGC.
  About 60 lines plus tests. The existing AM AGC and DC estimator carry over.
  Convention: LSB below 10 MHz, USB above (S: amateur practice; not read from a
  standard). This would also fix the 80m/40m `BANDS` entries.

### Squelch types

- Carrier/RSSI: `rtl_fm` computes DC-corrected RMS of the IQ block and zeroes
  the output below a threshold ([rtl_fm.c `rms`/`full_demod`](https://github.com/osmocom/rtl-sdr/blob/master/src/rtl_fm.c), P). GNU Radio: `pwr_squelch_cc/ff`, `simple_squelch_cc`
  ([list](https://github.com/gnuradio/gnuradio/tree/main/gr-analog/include/gnuradio/analog), P). radiotui has this (ADR-0008).
- Noise squelch: the classic analog-radio technique is to look at discriminator
  noise above the voice band (about 4-8 kHz): with no carrier, FM noise there
  is high; a real carrier quiets it. I did not find it as a named block in
  the GNU Radio list or in the SDR++ files I read (P), so this is general radio
  practice (S), not copied from a specific project.
  Feasibility: NumPy-trivial. The NFM channel rate is ~30 kS/s so the 4-8 kHz
  band exists; one FFT or a band-pass plus RMS per block. It would give a
  second opinion to the RSSI gate and better holds for the autonomous mode.
- Tone squelch: see CTCSS/DCS below.

## CTCSS and DCS

### CTCSS

- Algorithm (transmit): a continuous sub-audible sine at 67.0-254.1 Hz added
  at about 15% of system deviation (10-20% in engineered systems); a
  receiver unmutes only while its tone is present
  ([Wikipedia CTCSS](https://en.wikipedia.org/wiki/Continuous_Tone-Coded_Squelch_System), S). Standards: EIA RS-220-A (1979) through TIA-603-E (2016) (S, same page; the standard text is paywalled).
- Tone list (S: [Wikipedia](https://en.wikipedia.org/wiki/Continuous_Tone-Coded_Squelch_System), cross-checked against SDR++'s `CTCSS_TONES` table, [ctcss_squelch.h](https://github.com/AlexandreRouma/SDRPlusPlus/blob/master/core/src/dsp/noise_reduction/ctcss_squelch.h), P):
  67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4,
  100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5,
  141.3, 146.2, 150.0, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3,
  173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5,
  206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1.
  Wikipedia calls 39 of these the common EIA/TIA set; the rest are extras
  that decoders also list.
- Detection:
  - GNU Radio `ctcss_squelch_ff` runs three Goertzel filters (target tone and
    the neighbouring tones) over a 100 ms window and opens only if the target
    magnitude exceeds a level and both neighbours
    ([source](https://github.com/gnuradio/gnuradio/blob/main/gr-analog/lib/ctcss_squelch_ff_impl.cc), P).
  - SDR++ decodes the FM audio, resamples to 500 Hz, runs an FFT to estimate
    the tone frequency and snaps to the nearest standard tone
    ([ctcss_squelch.h](https://github.com/AlexandreRouma/SDRPlusPlus/blob/master/core/src/dsp/noise_reduction/ctcss_squelch.h), P). It supports "any tone", "none" and a specific tone.
- Spacing is the hard part: 150.0 and 151.4 Hz are 1.4 Hz apart, so a
  100 ms window (10 Hz bins) cannot separate them by FFT peak alone; use a
  longer window (about 1 s) when precision matters (E).
- Feasibility under ADR-0002: high. Low-pass and decimate the NFM
  discriminator output to ~1 kS/s (existing `decimate`), zero-padded
  `np.fft.rfft` of 1-2 s, peak pick in 60-260 Hz, parabolic interpolation,
  snap to table. About 80 lines. CPU negligible (E). Latency 0.5-1 s.
- Legal: none known; this is detection of an existing signal.
- Selector case: yes, but only partly. CTCSS is a squelch key, not a
  privacy feature: a receiver with squelch off hears everything
  ([Wikipedia](https://en.wikipedia.org/wiki/Continuous_Tone-Coded_Squelch_System), S). In practice it separates
  users that share a frequency (repeaters, shared business channels, PMR446
  sub-codes). A scanner that lets the listener hear only one tone does the
  same job as a radio's tone squelch.

### DCS

- Algorithm: a 134.4 bit/s FSK stream below 300 Hz carrying a repeating 23-bit
  Golay (23,12) codeword (12 data bits: 9 code bits written as three octal
  digits plus three fixed bits; 11 check bits), sent LSB first. About 83 of
  the 512 codes are used. Motorola calls it Digital Private Line, GE Digital
  Channel Guard. The 134.4 Hz stream can false-open 131.8 and 136.5 Hz CTCSS
  decoders ([Wikipedia](https://en.wikipedia.org/wiki/Continuous_Tone-Coded_Squelch_System), [SigID wiki](https://sigidwiki.com/wiki/DCS_squelch), [YO3IIU](https://yo3iiu.ro/blog/?p=753), all S). TIA-603 is the formal source (paywalled).
- Detection: low-pass below ~300 Hz, slice 134.4 bit/s with clock recovery, find
  the 23-bit word in the stream, correct up to 3 bit errors, match against
  the 83 codes in both polarities (some radios invert the stream; E).
- Feasibility: moderate. Precompute the 83 codewords and their complements
  (and cyclic rotations), then slide a Hamming-distance match over the sliced
  bits: pure NumPy, no per-sample loops beyond the slicer. About 150 lines
  (E). CPU negligible.
- Selector case: yes, same as CTCSS. A single "tone / code" picker can cover
  both.

## FM broadcast

### Stereo

- ITU-R BS.450-3 pilot-tone system: pilot at 19 kHz (exactly half the
  sub-carrier), 8-10% of peak deviation; L-R is double-sideband
  suppressed-carrier AM on 38 kHz +-4 Hz (S signal pre-emphasised like M);
  sub-carrier residual at most 1%; pilot phase tolerance 3 degrees
  ([BS.450-3 section 2.2](https://itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.450-3-200111-S!!PDF-E.pdf), P).
- GQRX has a `stereo_demod` block; SDR++ has `broadcast_fm.h` ([GQRX dsp dir](https://github.com/gqrx-sdr/gqrx/tree/master/src/dsp), [SDR++ demod dir](https://github.com/AlexandreRouma/SDRPlusPlus/tree/master/core/src/dsp/demod), P).
- Feasibility: moderate. radiotui's WFM channel rate is >=240 kS/s
  (`MIN_CHANNEL_RATE_HZ`), enough for the 0-60 kHz baseband plus RDS at 57
  kHz. A per-sample PLL is a Python loop, but the pilot is frequency stable
  (+-2 Hz class), so a block-wise estimate (band-pass 19 kHz by FFT, fit
  phase, synthesise `exp(j*2*phase)` for 38 kHz) is vectorisable. About 120
  lines (E). The audio pipeline is mono today; stereo also means a
  two-channel player, which is a larger change than the DSP.
- Value to a scanner user: low, since most listening is mono voice.

### RDS

- Spec: IEC 62106-1:2018 for the basic layer (modulation and baseband coding),
  paywalled ([IEC webstore](https://webstore.iec.ch/publication/60236), P for existence and scope; clause text not read). Its US variant is RBDS
  (NRSC-4-B), which I could not fetch.
- Parameters confirmed from redsea: 57 kHz subcarrier, 1187.5 bit/s,
  biphase symbols (two PSK symbols of opposite phase per bit), differential
  decoding, 104-bit groups of four blocks (`kBitsPerSecond = 1187.5f`,
  `oscillator.init(LIQUID_NCO, angularFreq(57000...))`, `BiphaseDecoder`,
  `DeltaDecoder`; [constants.hh](https://github.com/windytan/redsea/blob/master/src/constants.hh), [subcarrier.cc](https://github.com/windytan/redsea/blob/master/src/dsp/subcarrier.cc), P).
  Redsea's chain: AGC (500 Hz BW), 2.4 kHz low-pass FIR, RRC symbol sync
  (beta 0.8), NCO with PLL (bandwidth 0.03 Hz), then block sync.
  Decoded fields: PI (program identification, hex), PS (8-char station name),
  RadioText ([redsea package description](https://build.opensuse.org/package/show/hardware:sdr/redsea?expand=1), S; the 8 and 64 character lengths are from my knowledge of the spec, not read).
- Feasibility: moderate-large. Pieces: 57 kHz demod (coherent with 3x the
  19 kHz pilot, so no separate Costas loop is needed; E), matched filter,
  clock recovery, biphase/differential decode, 10-bit CRC syndrome block sync
  with offset words, group parser for 0A (PS) and 2A/2B (RT). 300-500
  lines (E). CPU is fine at 240 kS/s. Licence: redsea is permissive, so
  reading it is safe; the CRC and offset words come from the spec.
- Selector case: no. One RDS stream per FM carrier, so PI is an identity (and a
  dedupe key), not something to choose between. The useful output is naming
  the channel from PS/PI in the channel table.

## Digital voice and data

Summary table; details after.

| Mode | Where heard with RTL-SDR | Air interface | Open decoder(s), licence | NumPy-only? |
| --- | --- | --- | --- | --- |
| DMR | UHF/VHF business, ham, PMR446 tier I | 4FSK, 2-slot TDMA, 12.5 kHz | DSD (ISC), DSD-FME (mixed), SDRTrunk (GPL-3) | framing and metadata: yes, large; voice: no |
| dPMR | PMR446 digital | 4FSK 2400 sym/s, FDMA 6.25 kHz | DSD-FME | same |
| NXDN | business 6.25/12.5 kHz | 4FSK FDMA | DSD, DSD-FME | same |
| D-STAR | ham 2 m/70 cm | GMSK 4800 bit/s | DSD-FME | same (voice AMBE) |
| P25 Ph1/Ph2 | public safety | C4FM/CQPSK, H-DQPSK TDMA | OP25 (GPL-3), SDRTrunk | same |
| POCSAG/FLEX | pagers | 2-FSK 512/1200/2400 | multimon-ng (GPL-2) | yes, moderate |
| APRS/AX.25 | 144.390/144.800 MHz | Bell 202 AFSK 1200 | Dire Wolf (GPL-2), multimon-ng | yes, moderate |
| ADS-B | 1090 MHz | PPM 1 Mbit/s | dump1090 (BSD-3 and GPL forks) | yes, but needs 2 MS/s |
| AIS | 161.975/162.025 MHz | GMSK 9600 bit/s | AIS-catcher (GPL-3) | yes, moderate |
| ACARS | ~131 MHz | MSK 2400 on AM | acarsdec (GPL-2) | yes, moderate |

### DMR (ETSI TS 102 361)

- Spec: ETSI TS 102 361-1 V2.2.1 is free to download ([PDF](https://www.etsi.org/deliver/etsi_ts/102300_102399/10236101/02.02.01_60/ts_10236101v020201p.pdf), P). It defines a 2-slot TDMA
  structure on 12.5 kHz carriers (section 4.2), 4FSK, 30 ms bursts with
  264 content bits in 27.5 ms, 24-bit source and destination Logical Link IDs
  (section 9 tables), and a 216-bit "vocoder socket" payload (section 6.1).
  The codec itself is not defined in TS 102 361-1.
- Colour Code (CC): "a simple means of distinguishing overlapping sites... not
  used for addressing (individual or group)" ([section 4.2](https://www.etsi.org/deliver/etsi_ts/102300_102399/10236101/02.02.01_60/ts_10236101v020201p.pdf), P). Talkgroups are the 24-bit
  destination ID with a group-call flag.
- Multiple users on one frequency: yes, by design. Slot 1 and slot 2 carry
  independent calls on the same RF channel, and each call has a talkgroup.
  This is the clearest "needs a selector" case: the receiver must pick a slot
  and optionally a talkgroup.
- Feasibility: 4FSK slicing via the existing discriminator (4800 sym/s needs a
  channel rate of at least ~24 kS/s, which NFM already has), sync-word
  correlation (48-bit patterns), then FEC (BPTC, Hamming, Golay, QR; named in
  the spec). 800-1500 lines for CC/TG/slot/source metadata (E). No voice.
  Licence: DSD is ISC, so reading is safe; DSD-FME and SDRTrunk are GPL.
- Patent: voice is the problem, not framing. See Vocoders.

### dPMR, NXDN, D-STAR, P25

- dPMR: ETSI TS 102 490 (licence-free, hand-portable) and TS 102 658 (licensed
  tier); 4FSK, 2400 symbols/s (4800 bit/s), 6.25 kHz FDMA (S: [ETSI leaflet](https://www.etsi.org/images/files/ETSITechnologyLeaflets/digitalPrivateMobileRadio.pdf), [Wavecom](https://www.wavecom.ch/content/pdf/advanced_protocol_dpmr.pdf)). One call per channel, so no
  slot selector; a colour code and IDs exist.
- NXDN: 6.25 or 12.5 kHz 4-level FSK FDMA (S: [NXDN Forum](https://www.nxdn-forum.com/resources/data/Introduction_NXDN_IWCE%202019_FINAL.pdf)); a separate NXDN Forum deck gives a conflicting
  symbol rate (4800 symbols/s), so confirm against the specification before
  coding. Selector: RAN and unit/group IDs (S, not read).
- D-STAR: GMSK 4800 bit/s (not read from the spec; E).
- P25 Phase 1: C4FM or CQPSK at 4800 symbols/s per TIA-102.BAAA (now
  -B); Phase 2: two-slot TDMA in 12.5 kHz, H-DQPSK outbound; a 12-bit NAC
  prefixes every packet (S: [GNU Radio wiki PDF](https://wiki.gnuradio.org/images/f/f2/A_Look_at_Project_25_%28P25%29_Digital_Radio.pdf), [Project 25 standards list](https://project25.org/images/stories/ptig/P25_SC_25-04-001_P25_TIA_Standards_Accepted_April_2025.pdf)). TIA standards are paywalled. Selector: NAC and talkgroup; on
  trunked systems the voice channel moves under control-channel messages, so
  following a call needs a second tuner or a system that tolerates one dongle.
- OP25 (GPL-3) and SDRTrunk (GPL-3) are the reference implementations; they
  need GNU Radio or a JVM.
- Feasibility: all four have the same shape as DMR (2- or 4-level FSK, sync,
  FEC, ID extraction). P25 trunking is a different order of effort.

### POCSAG and FLEX

- POCSAG: ITU-R M.584-2 Annex 1, "Radio-paging code No. 1": preamble of at
  least 576 alternating bits, batches of a sync codeword plus 8 frames of 2
  codewords, BCH(31,21) plus parity per codeword, and the pager's frame is
  selected by the 3 least significant bits of its 21-bit identity ([M.584-2](https://www.itu.int/dms_pubrec/itu-r/rec/m/R-REC-M.584-2-199711-I!!PDF-E.pdf), P). Rates 512/1200/2400 bit/s as in multimon-ng's `demod_poc5/12/24.c`
  ([source dir](https://github.com/EliasOenal/multimon-ng), P).
- FLEX: Annex 2 of M.584-2 describes FLEX-TD (1.875 s frames, 15 cycles per
  hour, 128 frames per cycle, 1600 bit/s sync, +-4.8 kHz) ([M.584-2](https://www.itu.int/dms_pubrec/itu-r/rec/m/R-REC-M.584-2-199711-I!!PDF-E.pdf), P).
- Feasibility: POCSAG is small. NFM discriminator output is already suitable,
  then a bit slicer and BCH check; about 250 lines (E). FLEX is bigger.
- Selector case: addresses (RIC/capcode) filter the display; every page goes
  to a list, no need to "choose".
- Legal caveat (general, not from a cited source): pager traffic can be
  personal or medical, and many countries restrict intercepting or disclosing
  non-broadcast radio content; check local law before logging.

### APRS and AX.25

- Bell 202 AFSK, 1200 Hz mark and 2200 Hz space, 1200 baud, AX.25 UI frames,
  HDLC framing; 144.390 MHz North America, 144.800 MHz Europe (S:
  [Bell 202](https://en.wikipedia.org/wiki/Bell_202), [SigID APRS](https://www.sigidwiki.com/wiki/APRS)). AX.25 v2.2 (TAPR) and APRS101 are the specs; I could
  not retrieve them directly.
- Reference decoders: Dire Wolf `demod_afsk.c` and `hdlc_rec.c` (GPL-2+, P),
  multimon-ng `demod_afsk12.c` and `hdlc.c` (GPL-2, P; [file list](https://github.com/EliasOenal/multimon-ng)).
- Feasibility: high. Discriminate, correlate audio against 1200/2200 Hz
  quadrature references (a vectorised matched filter), recover clock (sweep
  phase offsets per frame), NRZI decode, HDLC flag and bit-unstuffing, CRC-16.
  About 250 lines (E). CPU negligible. Write from the spec; Dire Wolf is GPL.
- Selector case: callsign-SSID display filter; no need to choose.
- Legal: APRS is amateur and openly beaconed, so low risk.

### ADS-B

- 1090 MHz Mode S extended squitter, DF17, 112-bit frames with a 24-bit
  parity field (CRC remainder) and a 24-bit ICAO aircraft address
  ([mode-s.org](https://mode-s.org/1090mhz/content/mode-s/1-basics.html), P for frame sizes and parity; PPM and the preamble were not on those pages).
  antirez dump1090 documents 2 MS/s magnitude sampling, an 8 us preamble
  (`MODES_PREAMBLE_US 8`), default rate 2 MS/s, phase correction and
  bit-error tolerance ([README](https://github.com/antirez/dump1090/blob/master/README.md), [dump1090.c](https://github.com/antirez/dump1090/blob/master/dump1090.c), P).
- Feasibility under ADR-0002: the algorithm fits (magnitude, vectorised
  preamble correlation, 24-bit CRC via lookup, CPR position decode). But it
  needs 2 MS/s tuned to 1090 MHz, while radiotui scans at 1.024 MS/s and the
  sweeper owns the tuner (ADR-0004). It would be a separate mode of the app,
  not a demodulator. Out of scope for now.
- Selector: ICAO address as a display filter. Not a co-channel selector.

### AIS

- ITU-R M.1371-5: channels 161.975 and 162.025 MHz, 9600 bit/s +-50 ppm, NRZI,
  GMSK with transmit BT at most 0.4; user ID is the MMSI; self-organising
  TDMA ([M.1371-5 section 2](https://itu.int/dms_pubrec/itu-r/rec/m/R-REC-M.1371-5-201402-S!!PDF-E.pdf), P). A search result showed ITU listing M.1371-6 (02/2026) as current (S), so -5 is superseded.
- Reference: AIS-catcher (GPL-3) ([README](https://github.com/jvde-github/AIS-catcher), P).
- Feasibility: moderate. FM discriminator at >=48 kS/s, matched/low-pass filter
  at 9600 baud, clock recovery, NRZI, HDLC with CRC, then 6-bit payload
  decode for position reports (MMSI, position, speed). It shares an HDLC
  decoder with APRS. About 300 lines on top of that (E). Both channels fit in
  one 1.024 MS/s capture (50 kHz apart).
- Selector: no; vessels time-share the channel by TDMA. An MMSI table is the
  natural UI. `vhf_marine` (156-162.025 MHz) already covers the AIS channels.

### ACARS

- ~131 MHz VHF (region-specific channels), AM carrier, 2400 bit/s MSK with
  1200/2400 Hz tones, per ARINC 618 (paywalled) (S:
  [SigID](https://www.sigidwiki.com/wiki/ACARS), [Wavecom](https://www.wavecom.ch/content/ext/DecoderOnlineHelp/worddocuments/acars.htm)). acarsdec (GPL-2) is the open decoder ([README](https://github.com/f00b4r0/acarsdec), P).
- Feasibility: moderate (the AM envelope exists; add MSK matched filter,
  clock recovery, parity and CRC).
- Selector: aircraft registration display filter only.

## Vocoders

| Codec | Used by | Status | Open implementations |
| --- | --- | --- | --- |
| IMBE | P25 Phase 1 | core patents reported expired (S: [GopherTrunk](https://gophertrunk.org/vocoders.html), [RadioReference forum](https://forums.radioreference.com/threads/mbelib-imbe-ambe-patent-questions.437024/)) | mbelib (ISC), JMBE |
| AMBE / AMBE+2 | DMR, dPMR, NXDN, D-STAR, YSF, P25 Ph2 | DVSI patent US 8,359,197 ("Half-rate vocoder", filed 2003-04-01, granted 2013-01-22), Google's estimated expiry 2028-05-20, status shown active ([Google Patents](https://patents.google.com/patent/US8359197B2/en), P; Google calls this an assumption, not a legal conclusion; whether it covers every AMBE+2 implementation is unsettled) | mbelib (ISC code with a patent notice), JMBE |
| Codec2 | FreeDV, M17 | open design | LGPL-2.1 ([repo](https://github.com/drowe67/codec2), P) |

- mbelib's README says the source is "provided for educational purposes only"
  and that "executable objects compiled or derived from this package may be
  covered by one or more patents" ([README](https://github.com/szechyjs/mbelib/blob/master/README.md), P). SDRTrunk does not bundle its codec: users must
  fetch JMBE themselves and are told to check local law before "downloading,
  compiling, using, or DISTRIBUTING" it ([wiki](https://github.com/DSheirer/sdrtrunk/wiki/JMBE), P).
- The mbelib code licence (ISC) is compatible with radiotui's MIT licence. The
  obstacles are: patent risk, mbelib being C (a native dependency, so
  ADR-0002 would need reopening), and the "NumPy-only" rule.
- Codec2 is the only openly designed vocoder in this list. It is on air via
  M17 and FreeDV, both rare; the M17 demodulator I checked
  ([m17-cxx-demod](https://github.com/mobilinkd/m17-cxx-demod)) is GPL-3 (GitHub licence field).
- Conclusion: do not add voice decoding. Metadata-only digital decoding (who,
  which talkgroup, which slot) avoids the vocoder entirely.

## Co-channel "selector" cases

| Signal | Several users on one frequency? | Selector | Source |
| --- | --- | --- | --- |
| CTCSS | yes (tones are squelch keys) | tone (51 values) | S above |
| DCS | yes | code (about 83, plus inverted) | S above |
| DMR | yes: 2 slots, each with groups | slot + talkgroup (CC separates sites) | TS 102 361-1 (P) |
| dPMR | one call per 6.25 kHz | colour code / ID | S |
| NXDN | one call per channel | RAN / group | S |
| P25 Ph1 | one call, NAC + TG | NAC, talkgroup | S |
| P25 Ph2 | yes: 2 slots | slot + TG | S |
| RDS | no (one stream) | PI is an identity, not a choice | P (redsea) |
| POCSAG | yes (addresses) | RIC filter on display | M.584-2 (P) |
| APRS | yes (callsigns) | display filter | S |
| AIS | yes (TDMA) | MMSI display filter | M.1371-5 (P) |
| ADS-B | yes | ICAO display filter | P |

## Recommendation for radiotui

Ranked by value to someone listening with an RTL-SDR and by fit with ADR-0002.

1. **NFM sub-300 Hz high-pass, plus a noise squelch.** Fixes audible and
   recorded CTCSS/DCS rumble today, improves the VOX classifier's input, and
   gives the autonomous mode a second opinion. About 80 lines of NumPy FIR.
   Needs only a note under ADR-0002.
2. **SSB (USB/LSB) and CW, with LSB/USB defaults on the ham HF bands.**
   Currently 80m/40m are AM and unintelligible. About 60 lines reusing
   `frequency_shift`. Needs a small ADR: new `DemodMode` members, a per-band
   default, and entries in `MIN_CHANNEL_RATE_HZ` and `DEFAULT_CHANNEL_BW_HZ`.
3. **CTCSS detect and display, then an optional "required tone" filter**, then
   DCS. Detect on the decimated discriminator output, show the tone on the
   channel row, record it in exports. Needs an ADR for the new squelch type.
4. **Noise blanker (IQ, before decimation)** as an option for HF and low VHF.
5. **RDS PS/PI naming for WFM channels**; stereo only if a stereo player ever
   lands. Moderate effort; no selector.
6. **POCSAG, then APRS and AIS, via a shared HDLC/bit-slicer helper.** Only if
   the project wants a decoded-messages pane; check local interception law
   first for pagers.
7. **DMR/P25 metadata-only labelling** (colour code, slot, talkgroup, source
   ID) as a long-term item; large effort.

UI selectors needed:

- **Tone filter (items 1-3):** a control on the monitor view that cycles
  `off / any / <tone or code>`, in the style of the existing squelch controls
  (`tests/test_squelch_controls.py`). `off` = hear everything (default, same
  as a radio with tone squelch off), `any` = open on any detected valid tone,
  a value = open only on that CTCSS tone (67.0-254.1 Hz) or DCS code
  (`D023N`-style: octal code plus normal/inverted). Show the detected tone in
  the channel table. Persist per channel in the channels file (ADR-0005).
- **DMR slot/talkgroup picker (item 7):** `slot 1 / slot 2 / both` and a
  talkgroup field defaulting to "all". Not needed before DMR decoding exists.
- **Demod mode cycle:** none exists today (the mode comes from the band or
  `guess_demod`); add one that cycles `NFM / WFM / AM / USB / LSB / CW`.

Keep out of scope:

- **Vocoders (AMBE/AMBE+2/IMBE/Codec2) and mbelib.** Patent exposure
  (US 8,359,197, estimated expiry 2028), a C dependency against ADR-0002, and
  no way to test fidelity in pure NumPy. Metadata-only decoding gives most of
  the value.
- **Any GPL source in the tree** (GNU Radio, SDR++, GQRX, rtl_fm, multimon-ng,
  Dire Wolf, AIS-catcher, acarsdec, OP25, SDRTrunk, DSD-FME). Read for
  understanding only; implement from specs and the permissive references
  (antirez dump1090, redsea, DSD, mbelib's framing code).
- **ADS-B**: needs 2 MS/s at 1090 MHz and a different tuner owner, which
  conflicts with ADR-0004 in spirit. Better as a separate tool.
- **PLL-based discriminators and synchronous AM**: per-sample loops are slow
  in NumPy and the gain is marginal.
- **P25/DMR trunk following**: needs control-channel decoding plus a second
  tuner.

## Caveats on sources

- TIA-603 (CTCSS/DCS), IEC 62106 (RDS), ARINC 618 (ACARS), TIA-102 (P25) and
  ICAO Annex 10 (ADS-B) are paywalled or restricted; those facts come from
  open code and secondary pages, marked (S).
- Effort and CPU numbers are estimates and have not been benchmarked.
- Legal notes are cautions, not legal advice.
