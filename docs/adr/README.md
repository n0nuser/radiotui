# Architecture Decision Records

Numbered in the order the decision was made. Status is `Accepted` unless noted.

| ADR | Title | Date |
| --- | --- | --- |
| [0001](0001-pyrtlsdr-compat-shim.md) | pyrtlsdr compatibility shim over librtlsdr 2.x | 2026-08-22 |
| [0002](0002-numpy-only-receive-chain.md) | NumPy-only receive chain (no scipy) | 2026-08-22 |
| [0003](0003-channel-filter-decimation.md) | Channel filter via bandwidth-derived decimation and a triangular kernel | 2026-08-22 |
| [0004](0004-single-consumer-tuner.md) | Single-consumer tuner exclusivity | 2026-08-23 |
| [0005](0005-user-channels-file.md) | User channels live in a dedicated channels.toml | 2026-08-22 |
| [0006](0006-clip-sidecars-and-min-length.md) | Per-clip JSON sidecars with minimum-length discard | 2026-08-22 |
| [0007](0007-radio-first-tui.md) | Radio-first TUI: analyst panels opt-in, app-owned event history | 2026-08-23 |
| [0008](0008-rf-squelch-gate.md) | RF squelch gate for recordings on raw tuner RSSI | 2026-08-22 |
| [0009](0009-regional-radio-settings.md) | Regional band edges and configurable FM de-emphasis | 2026-08-23 |
| [0010](0010-classifier-vox-gate.md) | Audio classifier as a VOX noise gate | 2026-08-23 |
| [0011](0011-asynchronous-monitor-handoff.md) | Asynchronous monitor teardown with serialized tuner handoff | 2026-08-23 |
| [0012](0012-decoupled-reader-thread.md) | Decoupled reader thread so audio is produced at real time | 2026-08-23 |
| [0013](0013-windowed-sinc-channel-filter.md) | Windowed-sinc channel filter evaluated polyphase | 2026-08-23 |

Studies and alternatives investigated during these decisions live in
[`../research/`](../research/) and [`../reasoning_logs/`](../reasoning_logs/).
