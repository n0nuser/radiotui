# 0002 — NumPy-only receive chain (no scipy)

- Status: Accepted
- Date: 2026-08-22

## Context

The audio chain needed three classic DSP operations: anti-alias decimation,
single-pole de-emphasis, and channel filtering.
`scipy.signal.lfilter` is the textbook tool for the IIR parts, but scipy is a
heavy dependency for a project whose only numeric need so far was numpy.

## Alternatives considered

- Add scipy: fastest to write, ~60 MB of wheels for three functions.
- `np.cumsum` recursion for the IIR: numerically unstable because it requires
  dividing by `alpha**i`, which underflows/overflows within a block.
- FFT overlap-save FIR everywhere: unnecessary at our block sizes; direct
  convolution is already microseconds.
- Truncated-FIR approximation of the pole with carry-in state: vectorized,
  exact to <1e-4 after the transient, and state carries across blocks.

## Decision

Stay numpy-only.
De-emphasis uses `deemphasize_fir`, a truncated impulse response of
`y = alpha*y + x` whose tap count is derived from a decay floor (1e-5), with
the last taps' worth of input carried between blocks as state.
Decimation uses a triangular (cascaded double boxcar) kernel.
AM level control uses a slow per-block AGC instead of peak normalization.
A reference loop implementation (`deemphasize_loop`) is kept for equivalence
tests and benchmarking.

Measured in [the audio-chain study](../research/audio-chain-redesign.md):
equivalence <1e-4 after transient, >=3x faster than the Python loop
(in practice two orders of magnitude).

## Consequences

No scipy anywhere in the dependency tree.
Any future filter that genuinely needs an IIR must either extend the truncated-
FIR pattern or reopen this ADR.
