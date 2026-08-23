# ADR-0009: Regional radio settings

## Status

Accepted

## Decision

Built-in amateur band edges are selected with `[scanner] region`, using `r1`,
`r2`, or `r3`; the default is `r1`. WFM de-emphasis is selected independently
with `[audio] deemphasis_us`, accepting 50 or 75 microseconds and defaulting to
50.

## Rationale

Amateur allocations differ by ITU region, and FM broadcast pre-emphasis differs
between Europe and the Americas. Explicit settings are safer than pretending a
single preset fits every operator.
