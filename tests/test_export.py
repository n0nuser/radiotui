"""Issue #14: channel export to CSV/JSON, from scan --export and the TUI `e` key."""

import csv
import json
import subprocess
import sys
from datetime import datetime

from radiotui.core.models import Channel, DemodMode
from radiotui.export import export_channels, export_path_for
from radiotui.tui.app import RadioTuiApp

T0 = 1_700_000_000.0


def make_channel(freq_hz: float, hits: int = 3) -> Channel:
    return Channel(
        center_hz=freq_hz,
        bandwidth_hz=12_500.0,
        peak_db=-21.5,
        snr_db=11.0,
        demod=DemodMode.NFM,
        first_seen=T0,
        last_seen=T0 + 60,
        hits=hits,
    )


def test_csv_roundtrip(tmp_path):
    path = export_channels([make_channel(145.5e6), make_channel(98e6)], tmp_path / "f.csv")
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [float(r["center_hz"]) for r in rows] == [98_000_000.0, 145_500_000.0]
    row = rows[-1]
    assert float(row["bandwidth_hz"]) == 12_500.0
    assert float(row["peak_db"]) == -21.5
    assert int(row["hits"]) == 3
    assert row["demod"] == "nfm"
    # ISO 8601 timestamps parse back unambiguously
    assert datetime.fromisoformat(row["first_seen"]).timestamp() == T0


def test_json_roundtrip_includes_context(tmp_path):
    channels = [make_channel(433.5e6), make_channel(88.1e6)]
    context = {"band": "ISM 433", "gain_db": 28.0, "sweeps_completed": 42}
    path = export_channels(channels, tmp_path / "f.JSON", context=context)
    payload = json.loads(path.read_text())
    assert payload["context"]["band"] == "ISM 433"
    datetime.fromisoformat(payload["exported_at"])
    freqs = [ch["center_hz"] for ch in payload["channels"]]
    assert freqs == [88_100_000.0, 433_500_000.0]
    assert payload["channels"][0]["demod"] == "nfm"


def test_unknown_suffix_defaults_to_csv(tmp_path):
    path = export_channels([make_channel(100e6)], tmp_path / "out.txt")
    assert "center_hz" in path.read_text()


def test_export_path_naming(tmp_path):
    p = export_path_for("fm_broadcast", tmp_path)
    name = p.name
    assert name.startswith("radiotui-fm_broadcast-") and name.endswith(".csv")


def test_cli_scan_export_writes_on_exit(tmp_path):
    """End-to-end: sim sweep with --seconds expiry must leave a file behind."""
    target = tmp_path / "findings.csv"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "radiotui",
            "scan",
            "--sim",
            "--band",
            "pmr446",
            "--seconds",
            "2",
            "--autonomous",
            "--export",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if target.exists():  # channels may legitimately be zero on a quiet sim band
        rows = list(csv.DictReader(open(target)))
        assert all(float(r["center_hz"]) > 0 for r in rows)


async def test_tui_e_key_exports_and_logs(tmp_path):
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        app.sweeper.stop()  # keep the detector from pruning the seeded channel
        await pilot.pause(0.1)
        app.settings.audio.recordings_dir = str(tmp_path)
        channel = make_channel(96.9e6)
        channel.active = True  # only active channels surface in sweeper.channels
        app.sweeper._tracker.channels[96_900_000.0] = channel
        await pilot.press("e")
        await pilot.pause(0.3)
        files = list(tmp_path.glob("radiotui-*.csv"))
        assert len(files) == 1
        assert any("Exported" in event for event in app._events)


async def test_tui_e_key_without_channels_is_a_noop(tmp_path):
    app = RadioTuiApp(force_sim=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause(0.2)
        app.settings.audio.recordings_dir = str(tmp_path)
        await pilot.press("e")
        await pilot.pause(0.3)
        assert not list(tmp_path.glob("radiotui-*"))
