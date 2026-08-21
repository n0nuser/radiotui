import numpy as np
import pytest

from radiotui.antenna.advisor import analyze, format_report


def test_wavelength_math_2m_calling():
    report = analyze(145.5e6)
    assert report.wavelength_m == pytest.approx(299_792_458 / 145.5e6, rel=1e-6)
    assert report.quarter_wave_cm == pytest.approx(
        299_792_458 / 145.5e6 / 4 * 0.95 * 100, rel=1e-3
    )
    assert report.dipole_leg_cm == report.quarter_wave_cm
    assert report.dipole_total_cm == pytest.approx(2 * report.quarter_wave_cm)


def test_band_profile_selection():
    assert analyze(100e6).band_label == "FM broadcast"
    assert analyze(121.5e6).band_label == "Airband"
    assert analyze(145.5e6).band_label == "VHF high (2m)"
    assert analyze(446e6).band_label == "UHF (70cm)"


def test_higher_frequency_shorter_elements():
    low = analyze(145e6)
    high = analyze(446e6)
    assert high.quarter_wave_cm < low.quarter_wave_cm


def test_report_is_printable():
    text = format_report(analyze(446.00625e6))
    assert "MHz" in text and "Quarter wave" in text


def test_invalid_frequency():
    with pytest.raises(ValueError):
        analyze(0)
