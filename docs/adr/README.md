# Architecture Decision Records

Numbered in the order the decision was made. Status is `Accepted` unless noted.

| ADR | Title | Date |
| --- | --- | --- |
| [0001](0001-pyrtlsdr-compat-shim.md) | pyrtlsdr compatibility shim over librtlsdr 2.x | 2026-08-22 |
| [0002](0002-numpy-only-receive-chain.md) | NumPy-only receive chain (no scipy) | 2026-08-22 |
| [0003](0003-channel-filter-decimation.md) | Channel filter via bandwidth-derived decimation and a triangular kernel | 2026-08-22 |
| [0004](0004-single-consumer-tuner.md) | Single-consumer tuner exclusivity (enforcement amended by 0014) | 2026-08-23 |
| [0005](0005-user-channels-file.md) | User channels live in a dedicated channels.toml | 2026-08-22 |
| [0006](0006-clip-sidecars-and-min-length.md) | Per-clip JSON sidecars with minimum-length discard | 2026-08-22 |
| [0007](0007-radio-first-tui.md) | Radio-first TUI: analyst panels opt-in, app-owned event history | 2026-08-23 |
| [0008](0008-rf-squelch-gate.md) | RF squelch gate for recordings on raw tuner RSSI | 2026-08-22 |
| [0009](0009-regional-radio-settings.md) | Regional band edges and configurable FM de-emphasis | 2026-08-23 |
| [0010](0010-classifier-vox-gate.md) | Audio classifier as a VOX noise gate | 2026-08-23 |
| [0011](0011-asynchronous-monitor-handoff.md) | Asynchronous monitor teardown with serialized tuner handoff (superseded by 0014) | 2026-08-23 |
| [0012](0012-decoupled-reader-thread.md) | Decoupled reader thread so audio is produced at real time | 2026-08-23 |
| [0013](0013-windowed-sinc-channel-filter.md) | Windowed-sinc channel filter evaluated polyphase | 2026-08-23 |
| [0014](0014-tuner-session-owns-handoffs.md) | One session thread owns every tuner handoff | 2026-10-12 |
| [0015](0015-nfm-subaudible-highpass.md) | NFM sub-audible high-pass as a long linear-phase FIR | 2026-10-12 |
| [0016](0016-ssb-and-cw-modes.md) | SSB and CW by shift, complex low-pass and real part | 2026-10-12 |
| [0017](0017-ctcss-dcs-tone-squelch.md) | CTCSS/DCS detection and a tone squelch | 2026-10-12 |
| [0018](0018-nfm-noise-squelch.md) | NFM noise squelch on discriminator noise above the voice band | 2026-10-12 |

Studies and alternatives investigated during these decisions live in
[`../research/`](../research/) and [`../reasoning_logs/`](../reasoning_logs/).

## Adding an ADR

Write an ADR when a change picks between real alternatives, or reverses an
existing ADR (mark the old one `Superseded by NNNN`). Copy the shape of
[0004](0004-single-consumer-tuner.md): a Status and Date header, then the
Context, Alternatives considered, Decision and Consequences sections. Name the
file `NNNN-kebab-slug.md` with the next free number, and add a row to the
table above. Put the measurements that justify a decision in `../research/`
and link to them from the ADR.
