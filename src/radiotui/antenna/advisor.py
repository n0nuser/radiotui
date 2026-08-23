"""Antenna advisor: wavelength math and placement recommendations."""

from __future__ import annotations

from dataclasses import dataclass, field

SPEED_OF_LIGHT = 299_792_458.0
WIRE_VELOCITY_FACTOR = 0.95


@dataclass(frozen=True)
class AntennaReport:
    freq_hz: float
    band_label: str
    wavelength_m: float
    quarter_wave_cm: float
    dipole_total_cm: float
    dipole_leg_cm: float
    five_eighths_cm: float
    recommended: str
    polarization: str
    tips: list[str] = field(default_factory=list)


def _band_profile(freq_hz: float) -> tuple[str, str, str, list[str]]:
    mhz = freq_hz / 1e6
    if mhz < 30:
        return (
            "HF",
            "random wire (9:1 unun) or resonant dipole",
            "horizontal",
            [
                "NVIS: mount the dipole 0.1-0.2 lambda high for regional coverage",
                "longer is not better: match the wire to the band you care about",
            ],
        )
    if mhz < 88:
        return (
            "VHF low band",
            "discone or wideband vertical",
            "vertical",
            [
                "discone gives broad coverage but little gain: fine for scanning",
                "keep the feedline away from metal gutters and wiring",
            ],
        )
    if mhz < 108:
        return (
            "FM broadcast",
            "folded dipole or quarter-wave whip",
            "circular or mixed (try both orientations)",
            [
                "a T-shaped dipole cut to the table below works indoors near a window",
                "try both orientations: most FM transmitters use circular or mixed polarization",
            ],
        )
    if mhz < 137:
        return (
            "Airband",
            "vertical whip, discone or ground-plane antenna",
            "vertical",
            [
                "aircraft transmit vertically polarized: keep the whip truly vertical",
                "elevation beats gain: higher placement = longer line-of-sight range",
            ],
        )
    if mhz < 174:
        return (
            "VHF high",
            "quarter-wave ground plane or 2m dipole",
            "vertical",
            [
                "handheld-style whip: length matters, see quarter-wave below",
                "ground plane (radials at 90 degrees) sharpens the pattern upward-gain",
                "aim a directional yagi horizontally toward the transmitter of interest",
            ],
        )
    if mhz < 470:
        return (
            "UHF (70cm)",
            "quarter-wave whip, collinear or small yagi",
            "vertical",
            [
                "short cables matter: coax loss grows fast above 400 MHz",
                "a 3-5 element yagi pointed at the source adds several dB",
            ],
        )
    return (
        "SHF/microwave",
        "helical, patch or dish",
        "line-of-sight",
        ["precision alignment matters more than raw gain at these frequencies"],
    )


def analyze(freq_hz: float) -> AntennaReport:
    if freq_hz <= 0:
        raise ValueError("frequency must be positive")
    wavelength_m = SPEED_OF_LIGHT / freq_hz
    quarter_m = wavelength_m / 4 * WIRE_VELOCITY_FACTOR
    half_m = wavelength_m / 2 * WIRE_VELOCITY_FACTOR
    five_eighths_m = wavelength_m * 5 / 8 * WIRE_VELOCITY_FACTOR
    label, recommended, pol, tips = _band_profile(freq_hz)
    return AntennaReport(
        freq_hz=freq_hz,
        band_label=label,
        wavelength_m=wavelength_m,
        quarter_wave_cm=quarter_m * 100,
        dipole_total_cm=half_m * 100,
        dipole_leg_cm=quarter_m * 100,
        five_eighths_cm=five_eighths_m * 100,
        recommended=recommended,
        polarization=pol,
        tips=tips,
    )


def format_report(report: AntennaReport) -> str:
    lines = [
        f"Frequency      : {report.freq_hz / 1e6:.4f} MHz  ({report.band_label})",
        f"Wavelength     : {report.wavelength_m * 100:.1f} cm",
        f"Quarter wave   : {report.quarter_wave_cm:.1f} cm (whip/monopole element)",
        f"Dipole         : {report.dipole_total_cm:.1f} cm total, "
        f"{report.dipole_leg_cm:.1f} cm per leg",
        f"Five eighths   : {report.five_eighths_cm:.1f} cm (collinear section)",
        f"Recommended    : {report.recommended}",
        f"Polarization   : {report.polarization}",
        "",
        "Tips:",
    ]
    lines += [f"  - {tip}" for tip in report.tips]
    return "\n".join(lines)
