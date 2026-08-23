# Reasoning log — audio dropout hunt

Period: 2026-08-22 evening → 2026-08-23 morning.
Companion study with the distilled conclusions:
[../research/audio-dropout-forensics.md](../research/audio-dropout-forensics.md).
This log keeps the order of thought, including wrong turns.

## What the user reported

- First session: "music stops half a second each 2 seconds" while listening in
  the TUI.
- Next morning after my buffer fix: "still persists, each 8 seconds more or
  less".

The period change after enlarging the alsa buffer (341 ms → 1.4 s) was itself a
clue: a stall-based theory predicts rarer but longer dropouts once the buffer
is deeper. It kept stall theories alive and killed "fixed 2 s timer" theories.

## Elimination ladder as actually climbed

1. Instrumented the monitor loop headless: reads device-paced at 64 ms,
   demod p95 12 ms. No starvation. But this said nothing about playback.
2. Suspected the terminal: could not test directly, so I instrumented
   `AudioPlayer.write` inside a scripted real-TUI harness instead — writes every
   ~79 ms, max gap 116 ms. Terminal rendering exonerated *in harness*.
3. User A/B through PipeWire vs direct hw: clean both.
   PipeWire demoted from primary suspect; kept in mind as environment noise.
4. Recorder-on A/B (disk writes on the audio thread): clean.
5. USB autosuspend checked in sysfs: already disabled for the dongle.
6. rtl_test async run: 0 samples lost at 1.024 MS/s.
7. Built the dual-trace content detector (RF RSSI + audio RMS per block,
   WAV post-mortem) reasoning that silent-data injection would evade every
   timing probe while showing up as energy collapse. Ran 209 s of real radio:
   zero collapses, zero stalls, dips found were music dynamics.
8. Concurrency probe as a long shot (sweeper + monitor together): tuner threw
   `r82xx i2c wr failed=-9`, audio turned to garbage. Realized the product had
   exactly this path: `s` during a listen resumes the sweeper.

## Why it took a day

Every individual measurement was clean because each probe removed one suspect
*and one interaction*: the failure needed the user's live session (real keys)
plus the concurrent path plus load to be dramatic.
The dual-trace detector was built for this hunt but outlived it — it is now the
template for any future "audio stopped" report.

## Mistakes made along the way

- Trusted stdout markers inside Textual's capture layer (silent loss).
- Lost faulthandler dumps to pytest fd capture until `-s`.
- My own MRO-dispatching probe subclass manufactured a fake bug.
- `pkill -f soak_watch` matched its own shell command line during the soak and
  killed the tool call; pattern-matching kills must exclude self.

## Outcome

Policy fix (single-consumer tuner, abb47e6) plus robustness margins (buffer,
throttle). Root cause documented in the research study; user re-validation
pending as of this entry.
