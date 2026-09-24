import sys; sys.argv = [sys.argv[0]]
sys.path.insert(0, "/work/tools")
import numpy as np
from compare_rtc import read_gcov, _phase_corr_shift
gc = read_gcov("/data/capella_mexico_city/gcov/20240626/gcov_20240626.h5")
a = gc["gamma0"].astype(np.float64)
ok = np.isfinite(a) & (a > 0) & (gc["mask"] >= 1) & (gc["mask"] <= 254)
adb = np.where(ok, 10 * np.log10(np.where(ok, a, 1.0)), np.nan)

def fshift(t, dy, dx):
    ny, nx = t.shape
    ky = np.fft.fftfreq(ny)[:, None]; kx = np.fft.fftfreq(nx)[None, :]
    return np.real(np.fft.ifft2(np.fft.fft2(t) * np.exp(-2j * np.pi * (ky * dy + kx * dx))))

nan = float("nan")
for true in [(1.4028, 1.1057), (1.9334, 0.0), (0.0, 0.2126), (0.5, 0.5), (0.25, 0.1)]:
    rec = []
    for y0 in range(0, adb.shape[0] - 512, 256):
        for x0 in range(0, adb.shape[1] - 512, 256):
            big = adb[y0:y0 + 512, x0:x0 + 512]
            if np.mean(np.isfinite(big)) < 0.999:
                continue
            big = np.clip(np.where(np.isfinite(big), big, np.nanmean(big)), -30, 10)
            sh = fshift(big, *true)
            dy, dx, pk = _phase_corr_shift(big[128:384, 128:384], sh[128:384, 128:384])
            rec.append((dy, dx, pk))
    r = np.array(rec)
    my, mx = np.median(r[:, 0]), np.median(r[:, 1])
    ry = my / true[0] if true[0] else nan
    rx = mx / true[1] if true[1] else nan
    print(f"true ({true[0]:.4f}, {true[1]:.4f}) px -> median recovered ({my:.4f}, {mx:.4f}) ratio ({ry:.3f}, {rx:.3f}) peak {np.median(r[:, 2]):.3f} tiles {len(r)}")
