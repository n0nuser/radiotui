# ADR-0013: Windowed-sinc channel filter, evaluated polyphase

## Status

Accepted

## Decision

`decimate()` filters with a Hamming-windowed sinc sized `33 * factor` taps at a
`0.45 / factor` cutoff, evaluated polyphase so only the samples that survive
decimation are computed. WFM additionally low-passes the demodulated audio to
15 kHz before it is resampled to the output rate.

## Rationale

The cascaded-boxcar kernel from ADR-0003 placed its nulls on the fold
frequencies but had almost no stopband in between: a broadcast station 200 kHz
away was attenuated 22 dB, and an NFM channel 12.5 kHz away only 5 dB, where a
hardware receiver gives 50-70 dB. Neighbouring signals reached the discriminator
nearly intact, which is what made FM audibly noisier than a handheld radio on
the same station.

The windowed sinc measures 66 dB of rejection at 200 kHz with a flat passband
across the ±100 kHz channel. Polyphase evaluation keeps it affordable — cost
scales with the output length, so a 1123-tap filter costs ~2 ms per block
instead of the ~100 ms a full convolution would, which matters because
ADR-0012's consumer thread has to keep up with real time.

The 15 kHz audio low-pass stops the FM noise triangle and the 19 kHz stereo
pilot from folding into the audible band during the final resample; it measured
+5.8 dB of audio SNR on a noisy synthetic signal.

## Consequences

Adjacent-channel selectivity for NFM is still limited by the decimated rate: a
neighbour 12.5 kHz away lands inside the 30 kHz channel rate and cannot be
removed by the decimation filter alone. Rejecting it needs a second filter stage
at the channel rate, which is not implemented.
