"""Control run: patched MultiRTC with the v0.5.4 wavelength put back.

The patched run (``patched-matched``) and the runtime-subclass prototype
(``matched-tfix-rfix``) build bit-identical radar grids except for two things:
the wavelength (fixed only in the patch) and the orbit, whose state vectors are
sampled at ``sensing_start + k`` s with times rounded to 1 us by
``datetime.timedelta`` -- the prototype built its orbit before moving
``sensing_start``, so its rounding differs (0.082 us vs 0.094 us here).

This run keeps the patched orbit and grid and restores only
c / (Min + Max / 2). If it is identical to ``patched-matched``, the wavelength
does not reach the RTC output and the patched / prototype difference is the
orbit alone.

Usage (inside the dev container, patched install on PYTHONPATH)::

    PYTHONPATH=/data/external/multirtc-site-patched:$PYTHONPATH \
        MULTIRTC_CHECKOUT=/data/external/MultiRTC-a0edba8-patched \
        python artifacts/multirtc-sicd-fix-20261108/wavelength_control.py rtc SICD \
        --variant patched-stock-wavelength --dem DEM --resolution 5 --work-dir DIR
"""
import sys

sys.path.insert(0, "/work/tools")
import multirtc_capella_rtc as drv  # noqa: E402  (imports isce3 behind its argv guard)
import isce3  # noqa: E402
from multirtc.sicd import SicdRzdSlc  # noqa: E402


class PatchedStockWavelength(SicdRzdSlc):
    """Patched SicdRzdSlc with the v0.5.4 centre frequency ``Min + Max / 2``."""

    def __init__(self, sicd_path):
        super().__init__(sicd_path)
        f = self.source.RadarCollection.TxFrequency
        self.wavelength = isce3.core.speed_of_light / (f.Min + f.Max / 2)
        self.radar_grid = self.get_radar_grid()


drv.VARIANT_CLASS["patched-stock-wavelength"] = PatchedStockWavelength

if __name__ == "__main__":
    sys.exit(drv.main(sys.argv[1:]))
