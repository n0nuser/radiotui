# Research: better ways to tell signal from noise — round two

Period: 2026-08-24.
Follows [signal-vs-noise-detection.md](signal-vs-noise-detection.md), which
established the informational limits. This document investigates six leads that
study did not cover, on the premise that the limits are real but radiotui is not
yet near them.

Related decisions: [ADR-0002](../adr/0002-numpy-only-receive-chain.md),
[ADR-0008](../adr/0008-rf-squelch-gate.md),
[ADR-0012](../adr/0012-decoupled-reader-thread.md),
[ADR-0013](../adr/0013-windowed-sinc-channel-filter.md).

## Verdicts up front

| Lead | Verdict |
| --- | --- |
| CFAR detectors | **Mostly a dead end here** — but it diagnoses the real defect, which is cheaper to fix than CFAR |
| Spectral kurtosis | **Real, narrow, worth having** — a noise-power-independent statistic, but less sensitive than energy detection |
| SumThreshold / AOFlagger | **Dead end** — solves the opposite problem |
| Eigenvalue detection (MME/EME) | **Dead end here** — swamped by radiotui's own channel filter, measured below |
| Prior art (rtl_fm, GNU Radio, SDR++) | **Directly usable** — two implementations worth copying, one detail that reshapes #52 |
| The `np.interp` resampler | **A real bug, but not the one it looked like** — the dominant defect is a missing filter, not the resampler |
| Front-end handling | **One outright bug found**: PMR446 channels 8 and 9 are invisible on real hardware |

Everything below that carries a number was measured on this machine against the
code as it stands on `claude/codebase-review-issues-00wz0o`. Where a claim needs
hardware, it says so.

---

## 1. CFAR: the right diagnosis, the wrong cure

### What radiotui does now

`NoiseFloorEstimator` takes the 30th percentile of the **whole stitched frame**,
medians it over 32 sweeps, and adds a fixed `threshold_margin_db`
(`src/radiotui/dsp/detector.py`). One number for a spectrum assembled from up to
51 hops.

### What CFAR is

Radar's answer to the same problem. Rohling's
[1983 paper](https://ece.iisc.ac.in/~cmurthy/E1_244/Slides/Rohling.pdf) is the
standard reference for the ordered-statistic variant. The threshold is always

```
S = T·Z          (Rohling eq. 4)
```

where `Z` estimates local clutter power from a sliding reference window and `T`
sets the false-alarm rate. CA-CFAR takes `Z` as the mean of the window; OS-CFAR
takes `Z = X(k)`, the k-th value of the rank-ordered window (eq. 10). The
property that earns the name, in Rohling's words:

> From (13) a first important conclusion can be drawn, namely, that the scaling
> factor T controlling the false alarm probability Pfa does not depend on the
> average clutter power μ of the exponentially distributed parent population.
> Thus these methods may actually be considered as CFAR methods.

He recommends `k = 3N/4`: "A value of k about 3N/4 is well suited for practical
application", chosen over the ADT-optimal `7N/8` because it "leads to less
expansion of clutter areas". The price in homogeneous noise is small — Table IV
gives OS-vs-CA losses of 0.93 dB at N=16, 0.63 dB at N=24, 0.48 dB at N=32.

### Measured against the shipped detector

Simulated stitched sweeps: 8 hops × 1024 bins, each hop's bins drawn from a
Gamma distribution matching a 4-way-averaged periodogram, every detector
calibrated to the same measured false-alarm rate of 1e-3.

**Uniform floor — the shipped detector wins.**

| detector | SNR for Pd ≥ 0.9 |
| --- | --- |
| shipped (percentile over 8192 bins) | 3.0 dB |
| OS-CFAR N=16 | 6.5 dB |
| OS-CFAR N=32 | 5.0 dB |
| OS-CFAR N=64 | 4.0 dB |
| OS-CFAR N=256 | 3.5 dB |
| OS-CFAR N=512 | 3.0 dB |

That is Rohling's CFAR loss made concrete. The shipped estimator's "window" is
the entire frame, so its floor estimate has almost no variance, and OS-CFAR
needs N≈512 reference cells to catch up. **On a flat floor, pooling globally is
not a weakness — it is why the current detector is sensitive.**

**Non-uniform floor — the shipped detector collapses.** Same experiment, with a
hop-to-hop floor spread, detecting one weak station in a simulated FM-broadcast
scene (200-bin stations every ~520 bins):

| detector | 4 dB | 6 dB | 8 dB | 10 dB | 14 dB SNR |
| --- | --- | --- | --- | --- | --- |
| *6 dB hop spread* | | | | | |
| shipped | 0.29 | 0.50 | 0.88 | 1.00 | 1.00 |
| OS-CFAR N=256 g=220 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| **shipped + per-hop flatten** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** |
| *12 dB hop spread* | | | | | |
| shipped | 0.12 | 0.12 | 0.17 | 0.38 | 0.88 |
| CA-CFAR N=256 g=220 | 0.79 | 0.96 | 1.00 | 1.00 | 1.00 |
| OS-CFAR N=256 g=220 | 0.92 | 1.00 | 1.00 | 1.00 | 1.00 |
| **shipped + per-hop flatten** | **1.00** | **1.00** | **1.00** | **1.00** | **1.00** |

The shipped detector also stops being CFAR at all: calibrated to 1e-3 on a flat
floor, it measured 1.79e-3 once a 6 dB hop spread was introduced.

"Per-hop flatten" is five lines — divide each hop's bins by that hop's own
percentile floor before the existing detector runs. It recovers everything CFAR
recovers and keeps the large-window sensitivity CFAR gives up.

### Why CFAR loses despite being the textbook answer

Radar clutter varies *continuously and unpredictably* across the reference
window, which is what a sliding local estimator is for. radiotui's floor varies
**piecewise-constantly with known breakpoints** — the hop boundaries are in
`SweepPlan`. Once you use that structure, there is nothing left for a blind
local estimator to discover.

Three further measurements make CFAR actively unattractive here:

- **Self-masking is total.** OS-CFAR with N=32, guard=4 gives **Pd = 0.00** for a
  50-bin signal — the reference window sits entirely inside the signal, so the
  "floor" it measures *is* the signal. The guard band must exceed the widest
  signal: at 1 kHz bins a WFM station is 200 bins, so guard ≥ 220 and N ≥ 256.
  That is a 700-bin window, which then spans several neighbouring stations.
- **Per-hop CFAR windowing is worse than useless.** Requiring the whole window
  inside one hop blanks a window's width at each seam. Measured Pd = 0.00 across
  every SNR in the FM scene, because the weak station sat inside a blind zone.
  Per-hop *normalisation* is the right shape of that idea; per-hop *windowing*
  is not. This matters for [#53](https://github.com/n0nuser/radiotui/issues/53),
  which currently proposes the latter.
- **CA-CFAR is unusable in a crowded band** — but OS-CFAR is genuinely robust,
  exactly as Rohling claims. Narrow 8 dB signal, strong neighbour 10 bins away:

  | neighbour | shipped | CA-CFAR | GO-CFAR | OS-CFAR |
  | --- | --- | --- | --- | --- |
  | none | 1.00 | 1.00 | 1.00 | 1.00 |
  | +10 dB | 1.00 | 0.33 | 0.00 | 1.00 |
  | +20 dB | 1.00 | 0.00 | 0.00 | 1.00 |
  | +40 dB | 1.00 | 0.00 | 0.00 | 1.00 |

  If CFAR is ever adopted here it must be OS-CFAR. CA and GO are disqualified.

### Conclusion

CFAR is not the win. **Per-hop floor normalisation is**, and it is far cheaper.
CFAR earns its place only against floor structure *within* a hop — a rising
band edge, a strong wideband emitter lifting its neighbourhood — which has not
yet been shown to exist on this hardware. That is a hardware-validation question,
not a design one.

---

## 2. Radio-astronomy RFI excision

### Spectral kurtosis — real, and genuinely different

From [Nita & Gary (2010)](https://arxiv.org/abs/1005.4371), eq. 1, where `S1`
and `S2` accumulate the first two powers of `M` instantaneous PSD estimates:

```
SK = (M+1)/(M−1) · (M·S2/S1² − 1)
```

and for Gaussian noise "the estimator given by equation (1) is unbiased, i.e.
E(SK) = V²ₖ = 1". Implemented here and validated: **E[SK] = 0.97 on Gaussian
noise**, as the paper says.

This is a different kind of statistic from anything in `classify.py`. Every
existing cue — flatness, envelope modulation, speech-band share — is computed on
demodulated audio and compared against a tuned constant. SK is a **shape**
statistic on the raw spectrum with a *theoretical* expected value of exactly 1,
and it is scale-free: multiplying the input by any constant leaves it unchanged.

Mean SK in the signal bin, 64 spectra × 256 points = 16 ms at 1.024 MS/s:

| signal | 0 dB | 6 dB | 12 dB | 20 dB | 30 dB per-bin SNR |
| --- | --- | --- | --- | --- | --- |
| noise only | 0.97 | | | | |
| FM broadcast (steady) | 0.74 | 0.36 | 0.14 | 0.06 | 0.05 |
| keyed carrier | 0.71 | 0.33 | 0.10 | 0.02 | 0.00 |
| AM voice (350/900 Hz) | 0.79 | 0.52 | 0.36 | 0.30 | 0.29 |
| impulsive (1 frame in 16) | 5.00 | 10.68 | 13.99 | 15.23 | 15.45 |

Two things follow. **It does not dodge the SNR wall** — it needs ~6–12 dB
per-bin SNR to separate, where energy detection with a good floor works at −3 dB.
And **its immunity to noise-power uncertainty is exact, not approximate**:
detection curves were bit-identical at 0 dB, 2 dB and 6 dB of noise uncertainty,
while energy detection fell from Pd 1.00 to 0.49 at 0 dB SNR over the same range.

So SK trades sensitivity for floor-independence. That is a bad trade for the
sweep detector, where the floor is measurable. It is a good trade in two places:

- **Confirming a held channel** without any floor estimate at all — immune to
  the AGC and gain changes that wreck absolute-power thresholds (see §6).
- **Flagging impulsive interference.** SK ≈ 15 for impulsive input is an
  enormous, unambiguous signal, and nothing in radiotui currently distinguishes
  a switching-supply spike from a transmission.

It is *not* a birdie detector: a birdie is a steady carrier and looks exactly
like a station (SK < 1 for both).

### SumThreshold / AOFlagger — solves the opposite problem

[Offringa et al. (2010)](https://arxiv.org/abs/1002.1957) report SumThreshold as
the most accurate post-correlation classifier they tested, "with a theoretical
accuracy of 95% recognition and an approximately 0.1% false probability rate in
simple simulated cases". But note what it is for: it "estimates the astronomical
signal by carrying out a surface fit in the time-frequency plane".

Radio astronomy wants to **find and discard** man-made transmissions in order to
see the smooth background. radiotui wants to **find and keep** exactly those
transmissions. The algorithm transfers; the objective is inverted. Its output
would be a mask of everything radiotui is looking for.

There is a narrow real use — flagging the birdies and spurs the user currently
silences by hand through `UserChannels.ignored` — but that is a convenience
feature, not a detection improvement, and the iterative surface fit is a poor
fit for the real-time budget in ADR-0012. **Not recommended.**

---

## 3. Eigenvalue detection (MME/EME): swamped by our own filter

[Zeng & Liang (2008)](https://arxiv.org/abs/0804.2960) propose testing
`λmax/λmin` of the sample covariance matrix, and the claim is as advertised:

> The major advantage of the proposed methods over energy detection is as
> follows: energy detection needs the noise power for decision while the
> proposed methods do not need.

The catch is in their own signal model: "η(n) is the received white noise
assumed to be iid". They are explicit about what happens otherwise:

> Note: In above, we have assumed that the noise samples are white. In practice,
> if the received samples are the filtered outputs, the corresponding noise
> samples may be correlated.

**radiotui's samples are precisely "the filtered outputs".** Measured, on
noise only, with no signal present anywhere:

| input | L=4 | L=8 | L=16 |
| --- | --- | --- | --- |
| raw white noise (the paper's assumption) | 1.01 | 1.02 | 1.03 |
| after `decimate(factor=4)` | 1.85 | 4.06 | 18.94 |
| after `channel_decimate` WFM 200 kHz | 1.85 | 4.06 | 18.94 |
| after `channel_decimate` NFM 12.5 kHz | 1.79 | 4.10 | 20.56 |

For scale, on *white* noise a 0 dB SNR carrier moves the L=8 statistic to 5.07.
**radiotui's own channel filter colours the noise more than a 0 dB signal does.**
The detector would fire on empty spectrum, everywhere, always.

This is the Tandra & Sahai pattern from the previous study, playing out exactly:
the wall is not abolished, it is **relocated** — from uncertainty in the noise
*power* to uncertainty in the noise *whiteness*. Zeng & Liang offer pre-whitening
(their Appendix A), and radiotui's own filter response is known exactly so that
part is tractable — but the RTL2832U's internal decimation filter and analog
front-end shape are not published, so residual colouring would remain and would
have to be calibrated. That is strictly more work than calibrating the noise
floor, for a method whose entire selling point was avoiding calibration.

**Dead end.** Worth recording so it is not revisited.

---

## 4. Prior art: what the other SDR projects actually do

I read the implementations rather than the docs.

**`rtl_fm`** ([keenerd/rtl-sdr](https://github.com/keenerd/rtl-sdr/blob/master/src/rtl_fm.c))
computes RMS on the low-passed IQ **before demodulation** and compares against a
level derived from gain and downsample factor; when squelched it zeroes the
buffer. This is what ADR-0008 already does, one stage earlier.

**GNU Radio `pwr_squelch` / `simple_squelch`** are average-power-vs-threshold in
dB. Same idea again.

**GNU Radio `standard_squelch`** is the one worth copying — the actual dual-band
FM noise squelch, which nothing else in the SDR world seems to implement. Two
biquads, `b = (0.0193, 0, −0.0193)` with `a1 = 1.9524` (low) and `a1 = 1.3597`
(high), sharing `a2 = −0.9615`. Evaluating those responses:

| band | peak | −3 dB span | at 48 kHz |
| --- | --- | --- | --- |
| low (voice reference) | 0.01538·fs | 0.0126–0.0188·fs | 738 Hz, 606–900 Hz |
| high (hiss) | 0.12816·fs | 0.1250–0.1312·fs | 6152 Hz, 6001–6296 Hz |

At radiotui's exact 48 kHz output rate the hiss band lands at 6.0–6.3 kHz,
independently confirming the "above 6 kHz" figure the previous study took from a
secondary source.

Each band is squared to power and smoothed with a 100 ms single pole, and the
decision statistic is

```
(low − high) / (low + high)
```

through `threshold_ff(0.3, 0.43, 0)` — a Schmitt trigger closing at 0.3 and
opening at 0.43 — followed by a third 100 ms pole on the gate itself. The sign is
worth being careful about: the statistic rises toward +1 when the *hiss* band
collapses, which is what carrier capture does, so the gate opens on a **high**
value. `squelch_range()` returns `(0.0, 1.0)`: the user-facing threshold is a
dimensionless ratio, not a level.

**That normalisation is the point.** Dividing by the sum makes the statistic
independent of absolute level, so it needs no recalibration when gain changes —
exactly the failure mode ADR-0008 records for the raw-RSSI gate ("the number that
separates carrier from noise on one unit can differ on another"). [#52](https://github.com/n0nuser/radiotui/issues/52)
proposes high-passing and taking an RMS; it should specify the **ratio of two
bands**, not an absolute level in one.

**GNU Radio `ctcss_squelch_ff`** runs **three** Goertzel filters — centre plus
left and right guard bands at the adjacent CTCSS tone frequencies — and mutes
unless the centre beats the threshold *and* both guards:

```
d_mute = (d_out_c < d_level || d_out_c < d_out_l || d_out_c < d_out_r)
```

Guards sit at the *adjacent CTCSS tone frequencies* where those exist, falling
back to ±2% for non-standard or edge tones. Detection length defaults to
`rate/10`, i.e. 100 ms. #52 currently implies a bare Goertzel per tone against a
threshold; the guard-band comparison is what makes it robust against a signal
that is merely loud, and it costs two more Goertzel bins.

**A small correction while here.** The previous study and #52 both give the
standard CTCSS set as "38 standard tones, 67.0–254.1 Hz". GNU Radio's table is
38 tones running 67.0–**250.3** Hz. Both counts are 38; 254.1 belongs to the
extended tone sets some manufacturers add, not to the group being described.

**SDR++** uses an SNR-threshold squelch per its manual; **gqrx** is built on GNU
Radio and uses its power squelch. Neither implements noise squelch.

The synthesis: radiotui's current RSSI gate is squarely mainstream SDR practice.
Implementing #52's noise squelch would put it *ahead* of rtl_fm, gqrx and SDR++,
matching what hardware radios have always done — which is exactly the reason the
reporter's Baofeng behaves better ([#51](https://github.com/n0nuser/radiotui/issues/51)).

---

## 5. The resampler: a real bug, but the diagnosis was wrong

`channel_audio()` resamples with `np.interp`, which is linear interpolation and a
poor reconstruction filter. That much is true. But measuring it changed the
conclusion.

**Spur test.** A unit-amplitude tone placed above the audio band; the table gives
how much of it lands back *inside* the audio band, in dB relative to the tone.

NFM, 30117.6 → 48000 Hz:

| tone | shipped (`np.interp`, no audio LPF) | + 4 kHz audio LPF, still `np.interp` | + LPF and polyphase |
| --- | --- | --- | --- |
| 9.0 kHz | −32.9 dB | −134.6 dB | −183.0 dB |
| 11.0 kHz | −30.3 dB | −102.7 dB | −141.5 dB |
| 13.0 kHz | −27.5 dB | −102.8 dB | −156.3 dB |
| 14.5 kHz | −27.8 dB | −104.1 dB | −144.7 dB |

**The dominant defect is not the resampler — it is that the NFM path has no
audio low-pass at all.** WFM got one in ADR-0013 (`AUDIO_CUTOFF_HZ`, worth
+5.8 dB there); NFM never did. The FM noise triangle rises with frequency and
peaks right at the channel Nyquist, which is precisely where the resampler leaks
worst, so the two defects compound. Adding the low-pass NFM should have had all
along buys ~75 dB. Replacing the resampler buys a further ~45 dB — real, but
second-order once the filter exists.

For WFM, where the 15 kHz low-pass already runs, `np.interp` provides
essentially no anti-alias protection of its own (a 40 kHz component returns at
8 kHz only 1.0 dB down), so ADR-0013's low-pass is doing 100% of the work. That
explains why it measured as large a gain as it did.

**A polyphase replacement is affordable and exact.** The rates are exactly
rational because they derive from the same clock:

| mode | chan_fs | ratio to 48 kHz |
| --- | --- | --- |
| NFM | 1024000/34 = 30117.6 Hz | **51/32** |
| WFM | 256000 Hz | **3/16** |
| AM | 16000 Hz | **3/1** |

Evaluating only the surviving output phases (the same trick ADR-0013 already uses
for `decimate`), with 4 taps per phase:

| mode | `np.interp` | polyphase | spur rejection |
| --- | --- | --- | --- |
| NFM 51/32 | 0.041 ms | 0.255 ms | −81 dB (409 taps) |
| WFM 3/16 | 0.067 ms | 0.455 ms | −65 dB (129 taps) |

Under 0.5 ms per 64 ms block against ADR-0012's real-time budget, on top of the
~2 ms channel filter. Rational resampling also carries state exactly the way
`decimate` does, so issue #25's sample-exactness requirement is met — and it
removes the current `resample_input_samples`/`resample_output_samples` drift
bookkeeping, which exists only because `np.interp` cannot express a fixed ratio.

Recommended order: **NFM audio low-pass first** (small, mirrors existing WFM
code, ~75 dB), **polyphase second** (larger change, ~45 dB more).

---

## 6. Front-end: one real bug, and one thing that needs hardware

### PMR446 channels 8 and 9 are invisible. This is a bug.

`Sweeper._run` masks ±3% of the sample rate around every hop centre to hide the
DC/LO-leak spike, on real devices only. The hop grid is deterministic, so the
masked frequencies are the **same on every sweep, forever**:

| band | hops | masked | share of band |
| --- | --- | --- | --- |
| FM Broadcast | 27 | 829.4 kHz | 4.0% |
| Airband (AM) | 25 | 768.0 kHz | 4.0% |
| Marine VHF | 8 | 245.8 kHz | 4.1% |
| 70cm Amateur | 13 | 399.4 kHz | 4.0% |
| **PMR446** | **1** | **30.7 kHz** | **15.9%** |

PMR446 is a single hop centred at 446.103125 MHz, masking
446.0878–446.1185 MHz — which contains channel 8 (446.09375) and channel 9
(446.10625). Two of the sixteen PMR446 channels can never be detected on real
hardware. Marine VHF loses 16 channels' worth of spectrum, 70cm loses 26.

The fix is cheap: dither the hop centres by a fraction of the hop step between
sweeps so the blind zone moves and the channel tracker's persistence logic fills
it in. This is a genuine defect, not a tuning matter, and it is separable from
everything else in this document.

### Gain and AGC

`ScannerSettings.gain_db` defaults to `None`, which `RtlSdrDevice.set_gain_db`
turns into `sdr.gain = "auto"` — tuner AGC. On an R820T the single gain setting
drives LNA and mixer together, and letting it ride in the FM broadcast band is
the classic overload case. This is already noted under "Still open" in #51.

I can measure the *consequences* of overload on the simulator but not overload
itself: the simulator has no gain stage, no compression and no intermodulation,
so any number I produced would be describing my own model. **This needs
hardware**, and the measurement is straightforward: sweep gain from minimum to
auto on a strong local FM station, and record the noise floor and the SNR of a
weak station elsewhere in the band. Overload shows up as the floor rising faster
than the wanted signal.

Worth noting for whoever does it: the RTL2832U's *digital* AGC
(`rtlsdr_set_agc_mode`) is separate from the tuner AGC and is not currently
touched by radiotui at all.

### IQ imbalance and DC offset

Neither is corrected. Both are cheap and both are standard:

- **DC offset**: subtract the block mean from the IQ before demodulation. The
  monitor path does not do this; `demod_am` removes DC *after* envelope
  detection, which is not the same thing and does not help NFM or WFM.
- **IQ imbalance**: the standard blind correction estimates gain and phase
  mismatch from `E[I²]`, `E[Q²]` and `E[IQ]` over a block. Its symptom is an
  image of every signal mirrored about the centre frequency, typically 30–40 dB
  down on an RTL-SDR — which in a scanner shows up as **phantom channels**
  reflected about the hop centre.

The phantom-channel prediction is testable on hardware and I have not tested it.
If it is real it would look like a birdie the user silences by hand, which means
it may already have been observed and misattributed.

Offset tuning is correctly reported as unsupported: it is an E4000 feature, and
`set_offset_tuning` returning failure on an R820T unit is expected, not a bug.

---

## What this changes

| ticket | disposition |
| --- | --- |
| [#52](https://github.com/n0nuser/radiotui/issues/52) | Stands. Two corrections from prior art: the noise squelch must be a **ratio**, and CTCSS needs **guard bands**. |
| [#53](https://github.com/n0nuser/radiotui/issues/53) | Reshaped. Per-hop **normalisation**, not per-hop windowing — measured, the latter is worse than doing nothing. |
| [#54](https://github.com/n0nuser/radiotui/issues/54) | Unaffected, but §5 adds a second NFM audio defect in the same path. |
| [#55](https://github.com/n0nuser/radiotui/issues/55) | New. NFM audio low-pass + polyphase resampler (§5) |
| [#56](https://github.com/n0nuser/radiotui/issues/56) | New. Dither hop centres so the DC mask stops blinding fixed channels (§6) |

Not recommended: CFAR, SumThreshold, eigenvalue detection. Spectral kurtosis is
worth keeping in mind for impulse rejection but is not a detector upgrade.

## Sources

- Rohling, H., ["Radar CFAR Thresholding in Clutter and Multiple Target Situations"](https://ece.iisc.ac.in/~cmurthy/E1_244/Slides/Rohling.pdf),
  IEEE Trans. AES-19(4), 1983 — equations 4, 10, 13, 14 and Tables I–IV read
  from the paper itself.
- Nita, G. M. and Gary, D. E., ["The Generalized Spectral Kurtosis Estimator"](https://arxiv.org/abs/1005.4371),
  MNRAS Letters 406, 2010 — equations 1 and 7 quoted from the text.
- Offringa, A. R. et al., ["Post-correlation radio frequency interference classification methods"](https://arxiv.org/abs/1002.1957), 2010.
- Zeng, Y. and Liang, Y.-C., ["Eigenvalue based Spectrum Sensing Algorithms for Cognitive Radio"](https://arxiv.org/abs/0804.2960), 2008 —
  Algorithms 1 and 2, and the white-noise caveat, quoted from the text.
- [`rtl_fm.c`](https://github.com/keenerd/rtl-sdr/blob/master/src/rtl_fm.c) — squelch implementation read directly.
- [GNU Radio `standard_squelch.py`](https://github.com/gnuradio/gnuradio/blob/main/gr-analog/python/analog/standard_squelch.py) — filter coefficients read directly and evaluated.
- [GNU Radio `ctcss_squelch_ff_impl.cc`](https://github.com/gnuradio/gnuradio/blob/main/gr-analog/lib/ctcss_squelch_ff_impl.cc) — guard-band logic read directly.
- [GNU Radio `pwr_squelch_cc`](https://www.gnuradio.org/doc/doxygen/classgr_1_1analog_1_1pwr__squelch__cc.html) and [`simple_squelch_cc`](https://www.gnuradio.org/doc/doxygen/classgr_1_1analog_1_1simple__squelch__cc.html).
- [SDR++ user manual](https://www.sdrpp.org/manual.pdf) — SNR-threshold squelch.
- [rtl-sdr-blog gain control functions](https://deepwiki.com/rtlsdrblog/rtl-sdr-blog/6.3-gain-control-functions) — R820T LNA/mixer/VGA structure and the separate RTL2832U digital AGC.
