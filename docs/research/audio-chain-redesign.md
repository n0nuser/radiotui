# Research: audio chain redesign

Issue: #18.
Period: 2026-08-22.
Related decisions: [ADR-0002](../adr/0002-numpy-only-receive-chain.md),
[ADR-0003](../adr/0003-channel-filter-decimation.md).

## Baseline problems (measured)

- Fixed decimation left NFM with a ~128 kHz detection bandwidth against a
  12.5 kHz channel plan; adjacent PMR channels were indistinguishable.
- De-emphasis was a Python loop over every sample at the WFM intermediate rate
  (~256 kS/s), burning the GIL inside the monitor thread.
- AM normalized each block to its own peak, so level pumped between blocks.

## Alternatives investigated

### De-emphasis

| Option | Result |
| --- | --- |
| scipy `lfilter` | correct, rejected: new heavy dependency for one pole |
| `np.cumsum` recursion | rejected: requires dividing by `alpha**i`, numerically unstable within a block |
| Truncated-FIR pole with carry-in state | adopted |

Truncation length derived from a decay floor:
`span = ceil(log(1e-5) / log(alpha))` -> 138 taps at 240 kS/s for tau=50 us.
Verified equivalence against the reference loop: max error <1e-4 after the
400-sample transient; block-wise stitching with carried state matches the
continuous reference to <1e-3.
Benchmark: vectorized version >3x faster than the loop in the pinned test;
observed ~100x in practice.

### Channel filtering

| Option | Result |
| --- | --- |
| Windowed-sinc at full rate | thousands of taps needed for a useful transition band; too slow per 64 ms block |
| CIC / multi-stage halfbands | best stopband, too much machinery |
| Triangular kernel at full rate + shaping at channel rate | adopted |

The triangular kernel is two cascaded boxcars computed as one convolution;
its first null lands on the first alias of the stride.

### AM level

Slow per-block AGC adopted: gain chases `target/rms` clamped to [0.05, 16],
with a step detector (>12 dB jump tracks instantly, small drift smoothed 25%
per block) so level holds across blocks without flattening the modulation.

## Verification results

- Adjacent-channel suppression: carrier one PMR slot away (25 kHz) suppressed
  >=20 dB through the new chain (test-pinned); unfiltered baseline showed the
  two carriers indistinguishable.
- De-emphasis equivalence and benchmark as above.
- AM stability: 20 dB input step produces <6 dB output step.
- Regression found during testing: the legacy fixed 64-tap DC estimator spanned
  ~4 ms only at high rates; once AM channel rates dropped to ~16 kS/s it ate
  the modulation itself. Replaced with a rate-adaptive window (~4 ms).

## Outcome

Shipped in e6b9f18.
`Band.channel_bw_hz` drives real DSP; NFM now runs a ~30 kS/s channel rate
(factor 34), WFM stays at 4x, AM at 64x; HF's forced 250 kS/s remains valid.
