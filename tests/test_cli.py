"""Headless CLI commands end to end on the simulator."""

import threading

from radiotui.cli import main


def test_listen_honours_seconds():
    """`listen --seconds N` is advertised in --help; it must stop on its own."""
    done = threading.Event()
    result = []

    def run() -> None:
        result.append(main(["listen", "--sim", "--seconds", "1", "100M"]))
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(timeout=10.0), "listen ignored --seconds"
    assert result == [0]
