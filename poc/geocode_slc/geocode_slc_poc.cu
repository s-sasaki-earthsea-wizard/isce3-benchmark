// geocode_slc_poc.cu
//
// Measurement PoC for a prospective CUDA port of isce3::geocode::geocodeSlc
// (isce3-benchmark issue #11; gates the second upstream RFC).
//
// This is NOT the port. It isolates the two dominant compute patterns of
// isce3 cxx/isce3/geocode/geocodeSlc.cpp as minimal CUDA kernels plus
// OpenMP CPU references, on synthetic but realistically shaped data:
//
//   1. sinc interpolation (interpolate(), geocodeSlc.cpp:409):
//      per output pixel, gather a 9x9 chip at an irregular radar-grid
//      location, doppler-demodulate per chip row, 8x8 weighted sum against
//      the 8192x8 normalized sinc coefficient table (Sinc2dInterpolator),
//      doppler add-back.
//   2. reramp+flatten (carrierPhaseRerampAndFlatten(), geocodeSlc.cpp:299):
//      per output pixel, carrier poly eval + flatten phase
//      4*pi/wavelength * slantRange (~2e8 rad -> fp64-sensitive), sincos,
//      complex rotation of the geocoded pixel.
//
// Simplifications vs the real code (see README.md):
//   - geo2rdr and carrierPhaseDeramp phases are not modelled here. Deramp
//     is the same arithmetic pattern as flatten, evaluated over the input
//     grid instead of the output grid.
//   - The native doppler LUT2d eval is replaced by a bilinear polynomial
//     (same cost class); LUT2d.contains() by the NaN/bounds checks.
//   - The GPU interpolation kernel uses sincosf for the doppler factors
//     where the CPU code computes cos/sin in double and casts to float,
//     and accumulates row-first; the CPU-vs-GPU tolerance accounts for it.
//
// Measured quantities: CPU OpenMP time, GPU kernel-only time, H2D/D2H
// bytes and times, transfer-inclusive GPU time, CPU-vs-GPU agreement,
// fp32-vs-fp64 flatten phase error, working-set size.

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <cuda_runtime.h>
#include <omp.h>

#define CUDA_CHECK(call)                                                       \
    do {                                                                       \
        cudaError_t err_ = (call);                                             \
        if (err_ != cudaSuccess) {                                             \
            std::fprintf(stderr, "CUDA error '%s' at %s:%d\n",                 \
                    cudaGetErrorString(err_), __FILE__, __LINE__);             \
            std::exit(1);                                                      \
        }                                                                      \
    } while (0)

// Constants mirroring isce3/core/Constants.h
constexpr int SINC_HALF = 4;
constexpr int SINC_LEN = 8;
constexpr int SINC_ONE = 9;
constexpr int SINC_SUB = 8192;

struct Params {
    // Radar-grid block: sized like the Boso S1 IW3 in-burst SLC
    // (~1500 lines x ~24000 samples, cf32 ~ 275 MiB).
    int inRows = 1500;
    int inCols = 24000;
    // Output geogrid: the auto-derived Boso burst geogrid from
    // reports/2026-05-geocode-slc-profile.md.
    int outRows = 1046;
    int outCols = 645;
    int scale = 1;      // multiplies outRows/outCols (finer geogrid posting)
    int gpuReps = 30;
    int cpuReps = 3;
    const char* csv = nullptr;

    // Radar geometry, S1 C-band-like
    double wavelength = 0.0554658;
    double startingRange = 845000.0;
    double rangePixelSpacing = 2.329562;
    double prf = 1685.0;
    double sensingStart = 0.0;
};

// ---------------------------------------------------------------------------
// Small complex<float> helpers usable on host and device
// ---------------------------------------------------------------------------

__host__ __device__ inline float2 cmulf(float2 a, float2 b)
{
    return make_float2(a.x * b.x - a.y * b.y, a.x * b.y + a.y * b.x);
}

__host__ __device__ inline float2 cscalef(float2 a, float s)
{
    return make_float2(a.x * s, a.y * s);
}

__host__ __device__ inline float2 caddf(float2 a, float2 b)
{
    return make_float2(a.x + b.x, a.y + b.y);
}

// ---------------------------------------------------------------------------
// Synthetic inputs (deterministic, seed-free)
// ---------------------------------------------------------------------------

static inline uint64_t splitmix64(uint64_t x)
{
    x += 0x9E3779B97F4A7C15ULL;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
    return x ^ (x >> 31);
}

// Doppler (Hz) as a bilinear function of azimuth time / slant range —
// stand-in for the LUT2d<double> native-doppler eval in the real code.
__host__ __device__ inline double dopplerHz(
        double az, double rng, double startingRange)
{
    return 45.0 + 1.0e-5 * (rng - startingRange) + 0.8 * az;
}

// Combined range+azimuth carrier phase (rad) as a bilinear poly —
// stand-in for rgCarrierPhase.eval + azCarrierPhase.eval.
__host__ __device__ inline double carrierPhase(
        double az, double rng, double startingRange)
{
    const double dr = rng - startingRange;
    return 0.1 + 1.0e-3 * az + 2.0e-5 * dr + 1.0e-9 * az * dr;
}

__host__ __device__ inline float carrierPhaseF(
        float az, float rng, float startingRange)
{
    const float dr = rng - startingRange;
    return 0.1f + 1.0e-3f * az + 2.0e-5f * dr + 1.0e-9f * az * dr;
}

// Fill the radar block with deterministic pseudo-random cf32 in [-1, 1).
static void fillRadarBlock(std::vector<float2>& rdr, int rows, int cols)
{
#pragma omp parallel for
    for (int i = 0; i < rows; ++i) {
        for (int j = 0; j < cols; ++j) {
            const uint64_t h =
                    splitmix64((uint64_t)i * 2654435761ULL + (uint64_t)j);
            const float re = (float)((h & 0xFFFF) / 32768.0 - 1.0);
            const float im = (float)(((h >> 16) & 0xFFFF) / 32768.0 - 1.0);
            rdr[(size_t)i * cols + j] = make_float2(re, im);
        }
    }
}

// Geo->radar index maps. Mimics a geocoded burst footprint: a rotated,
// mildly nonlinear mapping whose image sits inside the radar grid, with
// NaN outside an elliptical footprint (invalid geo2rdr pixels).
static void fillIndexMaps(std::vector<double>& rgIdx, std::vector<double>& azIdx,
        int outRows, int outCols, int inRows, int inCols)
{
    const double nan = std::nan("");
#pragma omp parallel for
    for (int i = 0; i < outRows; ++i) {
        const double u = (outRows > 1) ? (double)i / (outRows - 1) : 0.0;
        for (int j = 0; j < outCols; ++j) {
            const double v = (outCols > 1) ? (double)j / (outCols - 1) : 0.0;
            const size_t k = (size_t)i * outCols + j;
            const double eu = (u - 0.5) / 0.62;
            const double ev = (v - 0.5) / 0.62;
            if (eu * eu + ev * ev > 1.0) {
                rgIdx[k] = nan;
                azIdx[k] = nan;
                continue;
            }
            azIdx[k] = 60.0 + 1050.0 * u + 180.0 * v + 25.0 * u * v;
            rgIdx[k] = 200.0 + 700.0 * u + 22600.0 * v + 300.0 * u * v;
        }
    }
    (void)inRows;
    (void)inCols;
}

// Port of Sinc2dInterpolator's constructor: _sinc_coef (beta=1, pedestal=0,
// weight=1) followed by per-offset normalization; layout table[ifrac][tap].
static void buildSincTable(std::vector<double>& table)
{
    const int decfactor = SINC_SUB;
    const int filtercoef = SINC_LEN * decfactor;
    std::vector<double> filter(filtercoef);
    const double wgthgt = (1.0 - 0.0) / 2.0;
    const double soff = (filtercoef - 1.0) / 2.0;
    for (int i = 0; i < filtercoef; ++i) {
        const double wgt =
                (1.0 - wgthgt) + wgthgt * std::cos((M_PI * (i - soff)) / soff);
        const double s = std::floor(i - soff) * 1.0 / (1.0 * decfactor);
        const double fct = (s != 0.0) ? (std::sin(M_PI * s) / (M_PI * s)) : 1.0;
        filter[i] = fct * wgt;
    }
    table.assign((size_t)decfactor * SINC_LEN, 0.0);
    for (int i = 0; i < decfactor; ++i) {
        double ssum = 0.0;
        for (int j = 0; j < SINC_LEN; ++j)
            ssum += filter[i + decfactor * j];
        for (int j = 0; j < SINC_LEN; ++j)
            table[(size_t)i * SINC_LEN + j] = filter[i + decfactor * j] / ssum;
    }
}

// ---------------------------------------------------------------------------
// CPU references (OpenMP) — faithful ports of the isce3 loops
// ---------------------------------------------------------------------------

// interpolate() (geocodeSlc.cpp:409) fused with Sinc2dInterpolator's
// _sinc_eval_2d, preserving the arithmetic order of the original.
static void interpCpu(const float2* rdr, float2* geo, const double* rgIdxArr,
        const double* azIdxArr, const double* table, const Params& p)
{
    const int inRows = p.inRows, inCols = p.inCols;
    const size_t outN = (size_t)p.outRows * p.outCols;
#pragma omp parallel for
    for (size_t ii = 0; ii < outN; ++ii) {
        geo[ii] = make_float2(0.f, 0.f);
        const double rgD = rgIdxArr[ii];
        const double azD = azIdxArr[ii];
        if (std::isnan(rgD) || std::isnan(azD))
            continue;
        const int intRg = (int)rgD;
        const int intAz = (int)azD;
        const double fracRg = rgD - intRg;
        const double fracAz = azD - intAz;
        if (intRg < SINC_HALF || intRg >= inCols - SINC_HALF)
            continue;
        if (intAz < SINC_HALF || intAz >= inRows - SINC_HALF)
            continue;

        const double rng = p.startingRange + rgD * p.rangePixelSpacing;
        const double az = p.sensingStart + azD / p.prf;
        const double doppFreq =
                dopplerHz(az, rng, p.startingRange) * 2.0 * M_PI / p.prf;

        // Build the doppler-demodulated 9x9 chip (complex<float>, as the
        // original: double cos/sin cast to float).
        std::complex<float> chip[SINC_ONE][SINC_ONE];
        for (int ci = 0; ci < SINC_ONE; ++ci) {
            const int row = intAz + ci - SINC_HALF;
            const double doppPhase = doppFreq * (ci - SINC_HALF);
            const std::complex<float> doppVal(
                    (float)std::cos(doppPhase), (float)-std::sin(doppPhase));
            for (int cj = 0; cj < SINC_ONE; ++cj) {
                const int col = intRg + cj - SINC_HALF;
                const float2 v = rdr[(size_t)row * inCols + col];
                chip[ci][cj] = std::complex<float>(v.x, v.y) * doppVal;
            }
        }

        // _sinc_eval_2d with intpx = intpy = 2*SINC_HALF = 8
        const int ifx = std::min(std::max(0, (int)(fracRg * SINC_SUB)),
                SINC_SUB - 1);
        const int ify = std::min(std::max(0, (int)(fracAz * SINC_SUB)),
                SINC_SUB - 1);
        std::complex<float> acc(0.f, 0.f);
        for (int ki = 0; ki < SINC_LEN; ++ki) {
            const float ky = (float)table[(size_t)ify * SINC_LEN + ki];
            for (int kj = 0; kj < SINC_LEN; ++kj) {
                const float kx = (float)table[(size_t)ifx * SINC_LEN + kj];
                acc += chip[2 * SINC_HALF - ki][2 * SINC_HALF - kj] * ky * kx;
            }
        }

        const double doppAddBack = doppFreq * fracAz;
        const std::complex<float> addBack(
                (float)std::cos(doppAddBack), (float)std::sin(doppAddBack));
        const std::complex<float> out = acc * addBack;
        geo[ii] = make_float2(out.real(), out.imag());
    }
}

// carrierPhaseRerampAndFlatten() (geocodeSlc.cpp:299), fp64 phase path,
// reramp=true, flatten=true, corrected slant range. In-place on geo.
static void flattenCpu(float2* geo, const double* rgIdxArr,
        const double* azIdxArr, const Params& p)
{
    const int inRows = p.inRows, inCols = p.inCols;
    const int chipHalf = SINC_ONE / 2;
    const size_t outN = (size_t)p.outRows * p.outCols;
#pragma omp parallel for
    for (size_t ii = 0; ii < outN; ++ii) {
        const double rgD = rgIdxArr[ii];
        const double azD = azIdxArr[ii];
        if (std::isnan(rgD) || std::isnan(azD))
            continue;
        const int intRg = (int)rgD;
        const int intAz = (int)azD;
        if (intRg < chipHalf || intRg >= inCols - chipHalf)
            continue;
        if (intAz < chipHalf || intAz >= inRows - chipHalf)
            continue;

        const double rng = p.startingRange + rgD * p.rangePixelSpacing;
        const double az = p.sensingStart + azD / p.prf;
        const double carrier = carrierPhase(az, rng, p.startingRange);
        const double flattenPh = 4.0 * (M_PI / p.wavelength) * rng;
        const double totalPhase = carrier + flattenPh;
        const std::complex<float> rot(
                (float)std::cos(totalPhase), (float)std::sin(totalPhase));
        const std::complex<float> g(geo[ii].x, geo[ii].y);
        const std::complex<float> out = g * rot;
        geo[ii] = make_float2(out.real(), out.imag());
    }
}

// ---------------------------------------------------------------------------
// GPU kernels
// ---------------------------------------------------------------------------

__global__ void interpKernel(const float2* __restrict__ rdr,
        float2* __restrict__ geo, const double* __restrict__ rgIdxArr,
        const double* __restrict__ azIdxArr, const double* __restrict__ table,
        int inRows, int inCols, size_t outN, double startingRange,
        double rangePixelSpacing, double prf, double sensingStart)
{
    const size_t ii = (size_t)blockIdx.x * blockDim.x + threadIdx.x;
    if (ii >= outN)
        return;
    geo[ii] = make_float2(0.f, 0.f);
    const double rgD = rgIdxArr[ii];
    const double azD = azIdxArr[ii];
    if (isnan(rgD) || isnan(azD))
        return;
    const int intRg = (int)rgD;
    const int intAz = (int)azD;
    const double fracRg = rgD - intRg;
    const double fracAz = azD - intAz;
    if (intRg < SINC_HALF || intRg >= inCols - SINC_HALF)
        return;
    if (intAz < SINC_HALF || intAz >= inRows - SINC_HALF)
        return;

    const double rng = startingRange + rgD * rangePixelSpacing;
    const double az = sensingStart + azD / prf;
    const double doppFreq =
            dopplerHz(az, rng, startingRange) * 2.0 * M_PI / prf;

    const int ifx = min(max(0, (int)(fracRg * SINC_SUB)), SINC_SUB - 1);
    const int ify = min(max(0, (int)(fracAz * SINC_SUB)), SINC_SUB - 1);

    float2 acc = make_float2(0.f, 0.f);
    for (int ki = 0; ki < SINC_LEN; ++ki) {
        const int chipRowOff = SINC_HALF - ki; // = (8 - ki) - SINC_HALF
        const int row = intAz + chipRowOff;
        float sd, cd;
        sincosf((float)(doppFreq * chipRowOff), &sd, &cd);
        const float2 doppVal = make_float2(cd, -sd);
        const float ky = (float)__ldg(&table[(size_t)ify * SINC_LEN + ki]);
        float2 rowAcc = make_float2(0.f, 0.f);
        for (int kj = 0; kj < SINC_LEN; ++kj) {
            const int col = intRg + SINC_HALF - kj;
            const float kx = (float)__ldg(&table[(size_t)ifx * SINC_LEN + kj]);
            const float2 v = __ldg(&rdr[(size_t)row * inCols + col]);
            rowAcc = caddf(rowAcc, cscalef(v, kx));
        }
        acc = caddf(acc, cscalef(cmulf(rowAcc, doppVal), ky));
    }

    float sa, ca;
    sincosf((float)(doppFreq * fracAz), &sa, &ca);
    geo[ii] = cmulf(acc, make_float2(ca, sa));
}

// fp64 phase path — the faithful port of the CPU flatten arithmetic.
__global__ void flattenKernelF64(float2* __restrict__ geo,
        const double* __restrict__ rgIdxArr,
        const double* __restrict__ azIdxArr, int inRows, int inCols,
        size_t outN, double startingRange, double rangePixelSpacing,
        double prf, double sensingStart, double wavelength)
{
    const size_t ii = (size_t)blockIdx.x * blockDim.x + threadIdx.x;
    if (ii >= outN)
        return;
    const double rgD = rgIdxArr[ii];
    const double azD = azIdxArr[ii];
    if (isnan(rgD) || isnan(azD))
        return;
    const int chipHalf = SINC_ONE / 2;
    const int intRg = (int)rgD;
    const int intAz = (int)azD;
    if (intRg < chipHalf || intRg >= inCols - chipHalf)
        return;
    if (intAz < chipHalf || intAz >= inRows - chipHalf)
        return;

    const double rng = startingRange + rgD * rangePixelSpacing;
    const double az = sensingStart + azD / prf;
    const double totalPhase = carrierPhase(az, rng, startingRange) +
                              4.0 * (M_PI / wavelength) * rng;
    double s, c;
    sincos(totalPhase, &s, &c);
    geo[ii] = cmulf(geo[ii], make_float2((float)c, (float)s));
}

// Naive fp32 phase path — measures both the fp64->fp32 speed delta and the
// phase error caused by evaluating ~2e8 rad in single precision.
__global__ void flattenKernelF32(float2* __restrict__ geo,
        const double* __restrict__ rgIdxArr,
        const double* __restrict__ azIdxArr, int inRows, int inCols,
        size_t outN, float startingRange, float rangePixelSpacing, float prf,
        float sensingStart, float wavelength)
{
    const size_t ii = (size_t)blockIdx.x * blockDim.x + threadIdx.x;
    if (ii >= outN)
        return;
    const double rgD = rgIdxArr[ii];
    const double azD = azIdxArr[ii];
    if (isnan(rgD) || isnan(azD))
        return;
    const int chipHalf = SINC_ONE / 2;
    const int intRg = (int)rgD;
    const int intAz = (int)azD;
    if (intRg < chipHalf || intRg >= inCols - chipHalf)
        return;
    if (intAz < chipHalf || intAz >= inRows - chipHalf)
        return;

    const float rng = startingRange + (float)rgD * rangePixelSpacing;
    const float az = sensingStart + (float)azD / prf;
    const float totalPhase = carrierPhaseF(az, rng, startingRange) +
                             4.0f * ((float)M_PI / wavelength) * rng;
    float s, c;
    sincosf(totalPhase, &s, &c);
    geo[ii] = cmulf(geo[ii], make_float2(c, s));
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

struct GpuTimer {
    cudaEvent_t beg, end;
    GpuTimer()
    {
        CUDA_CHECK(cudaEventCreate(&beg));
        CUDA_CHECK(cudaEventCreate(&end));
    }
    ~GpuTimer()
    {
        cudaEventDestroy(beg);
        cudaEventDestroy(end);
    }
    void start() { CUDA_CHECK(cudaEventRecord(beg)); }
    float stopMs()
    {
        CUDA_CHECK(cudaEventRecord(end));
        CUDA_CHECK(cudaEventSynchronize(end));
        float ms = 0.f;
        CUDA_CHECK(cudaEventElapsedTime(&ms, beg, end));
        return ms;
    }
};

static void usage(const char* argv0)
{
    std::printf(
            "usage: %s [--in-rows N] [--in-cols N] [--out-rows N] "
            "[--out-cols N]\n"
            "          [--scale N] [--gpu-reps N] [--cpu-reps N] "
            "[--csv PATH]\n",
            argv0);
}

int main(int argc, char** argv)
{
    Params p;
    for (int a = 1; a < argc; ++a) {
        auto next = [&](const char* name) -> const char* {
            if (a + 1 >= argc) {
                std::fprintf(stderr, "missing value for %s\n", name);
                std::exit(2);
            }
            return argv[++a];
        };
        if (!std::strcmp(argv[a], "--in-rows"))
            p.inRows = std::atoi(next("--in-rows"));
        else if (!std::strcmp(argv[a], "--in-cols"))
            p.inCols = std::atoi(next("--in-cols"));
        else if (!std::strcmp(argv[a], "--out-rows"))
            p.outRows = std::atoi(next("--out-rows"));
        else if (!std::strcmp(argv[a], "--out-cols"))
            p.outCols = std::atoi(next("--out-cols"));
        else if (!std::strcmp(argv[a], "--scale"))
            p.scale = std::atoi(next("--scale"));
        else if (!std::strcmp(argv[a], "--gpu-reps"))
            p.gpuReps = std::atoi(next("--gpu-reps"));
        else if (!std::strcmp(argv[a], "--cpu-reps"))
            p.cpuReps = std::atoi(next("--cpu-reps"));
        else if (!std::strcmp(argv[a], "--csv"))
            p.csv = next("--csv");
        else {
            usage(argv[0]);
            return 2;
        }
    }
    p.outRows *= p.scale;
    p.outCols *= p.scale;

    const size_t inN = (size_t)p.inRows * p.inCols;
    const size_t outN = (size_t)p.outRows * p.outCols;

    cudaDeviceProp prop;
    CUDA_CHECK(cudaGetDeviceProperties(&prop, 0));
    std::printf("== geocode_slc PoC microbenchmark (issue #11) ==\n");
    std::printf("GPU: %s (sm_%d%d), CPU threads: %d\n", prop.name, prop.major,
            prop.minor, omp_get_max_threads());
    std::printf("radar block: %d x %d (%.1f MiB cf32), geogrid: %d x %d "
                "(%zu px)\n",
            p.inRows, p.inCols, inN * sizeof(float2) / 1048576.0, p.outRows,
            p.outCols, outN);

    // --- inputs ------------------------------------------------------------
    std::vector<float2> rdr(inN);
    std::vector<double> rgIdx(outN), azIdx(outN), table;
    fillRadarBlock(rdr, p.inRows, p.inCols);
    fillIndexMaps(rgIdx, azIdx, p.outRows, p.outCols, p.inRows, p.inCols);
    buildSincTable(table);

    // Valid-pixel mask + working set (bounding box of touched radar chips)
    size_t validN = 0;
    int azMin = p.inRows, azMax = -1, rgMin = p.inCols, rgMax = -1;
    std::vector<uint8_t> valid(outN, 0);
    for (size_t k = 0; k < outN; ++k) {
        const double r = rgIdx[k], z = azIdx[k];
        if (std::isnan(r) || std::isnan(z))
            continue;
        const int ir = (int)r, iz = (int)z;
        if (ir < SINC_HALF || ir >= p.inCols - SINC_HALF)
            continue;
        if (iz < SINC_HALF || iz >= p.inRows - SINC_HALF)
            continue;
        valid[k] = 1;
        ++validN;
        azMin = std::min(azMin, iz - SINC_HALF);
        azMax = std::max(azMax, iz + SINC_HALF);
        rgMin = std::min(rgMin, ir - SINC_HALF);
        rgMax = std::max(rgMax, ir + SINC_HALF);
    }
    const double wsBboxMiB = (azMax >= azMin && rgMax >= rgMin)
            ? (double)(azMax - azMin + 1) * (rgMax - rgMin + 1) *
                    sizeof(float2) / 1048576.0
            : 0.0;
    const double wsRowsMiB = (azMax >= azMin)
            ? (double)(azMax - azMin + 1) * p.inCols * sizeof(float2) /
                    1048576.0
            : 0.0;
    std::printf("valid px: %zu / %zu (%.1f%%), working set: bbox %.1f MiB, "
                "row-span %.1f MiB\n",
            validN, outN, 100.0 * validN / outN, wsBboxMiB, wsRowsMiB);

    // --- CPU references ----------------------------------------------------
    std::vector<float2> geoCpu(outN), geoFlatCpu(outN);
    double interpCpuMs = 1e30, flattenCpuMs = 1e30;
    for (int r = 0; r < p.cpuReps; ++r) {
        const double t0 = omp_get_wtime();
        interpCpu(rdr.data(), geoCpu.data(), rgIdx.data(), azIdx.data(),
                table.data(), p);
        const double t1 = omp_get_wtime();
        interpCpuMs = std::min(interpCpuMs, (t1 - t0) * 1e3);
    }
    for (int r = 0; r < p.cpuReps; ++r) {
        geoFlatCpu = geoCpu; // flatten mutates in place; restore per rep
        const double t0 = omp_get_wtime();
        flattenCpu(geoFlatCpu.data(), rgIdx.data(), azIdx.data(), p);
        const double t1 = omp_get_wtime();
        flattenCpuMs = std::min(flattenCpuMs, (t1 - t0) * 1e3);
    }

    // --- GPU buffers + transfers -------------------------------------------
    float2 *dRdr = nullptr, *dGeo = nullptr, *dGeoIn = nullptr;
    double *dRg = nullptr, *dAz = nullptr, *dTable = nullptr;
    CUDA_CHECK(cudaMalloc(&dRdr, inN * sizeof(float2)));
    CUDA_CHECK(cudaMalloc(&dGeo, outN * sizeof(float2)));
    CUDA_CHECK(cudaMalloc(&dGeoIn, outN * sizeof(float2)));
    CUDA_CHECK(cudaMalloc(&dRg, outN * sizeof(double)));
    CUDA_CHECK(cudaMalloc(&dAz, outN * sizeof(double)));
    CUDA_CHECK(cudaMalloc(&dTable, table.size() * sizeof(double)));

    const size_t h2dBytes = inN * sizeof(float2) + 2 * outN * sizeof(double) +
            table.size() * sizeof(double);
    const size_t d2hBytes = outN * sizeof(float2);

    GpuTimer timer;
    timer.start();
    CUDA_CHECK(cudaMemcpy(dRdr, rdr.data(), inN * sizeof(float2),
            cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(dRg, rgIdx.data(), outN * sizeof(double),
            cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(dAz, azIdx.data(), outN * sizeof(double),
            cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(dTable, table.data(), table.size() * sizeof(double),
            cudaMemcpyHostToDevice));
    const float h2dMs = timer.stopMs();

    const int block = 128;
    const int grid = (int)((outN + block - 1) / block);

    // --- interp kernel -----------------------------------------------------
    auto runInterp = [&]() {
        interpKernel<<<grid, block>>>(dRdr, dGeo, dRg, dAz, dTable, p.inRows,
                p.inCols, outN, p.startingRange, p.rangePixelSpacing, p.prf,
                p.sensingStart);
    };
    for (int w = 0; w < 3; ++w)
        runInterp();
    CUDA_CHECK(cudaDeviceSynchronize());
    CUDA_CHECK(cudaGetLastError());
    float interpGpuMs = 1e30f;
    for (int r = 0; r < p.gpuReps; ++r) {
        timer.start();
        runInterp();
        interpGpuMs = std::min(interpGpuMs, timer.stopMs());
    }

    // Use the CPU interp result as the flatten input on BOTH sides so the
    // flatten validation is not polluted by interp CPU/GPU differences.
    CUDA_CHECK(cudaMemcpy(dGeoIn, geoCpu.data(), outN * sizeof(float2),
            cudaMemcpyHostToDevice));

    std::vector<float2> geoGpu(outN);
    timer.start();
    CUDA_CHECK(cudaMemcpy(geoGpu.data(), dGeo, outN * sizeof(float2),
            cudaMemcpyDeviceToHost));
    const float d2hMs = timer.stopMs();

    // --- flatten kernels ---------------------------------------------------
    auto timeFlatten = [&](auto&& launch) {
        // warmup
        CUDA_CHECK(cudaMemcpy(dGeo, dGeoIn, outN * sizeof(float2),
                cudaMemcpyDeviceToDevice));
        launch();
        CUDA_CHECK(cudaDeviceSynchronize());
        CUDA_CHECK(cudaGetLastError());
        float best = 1e30f;
        for (int r = 0; r < p.gpuReps; ++r) {
            CUDA_CHECK(cudaMemcpy(dGeo, dGeoIn, outN * sizeof(float2),
                    cudaMemcpyDeviceToDevice));
            timer.start();
            launch();
            best = std::min(best, timer.stopMs());
        }
        return best;
    };

    const float flatten64GpuMs = timeFlatten([&]() {
        flattenKernelF64<<<grid, block>>>(dGeo, dRg, dAz, p.inRows, p.inCols,
                outN, p.startingRange, p.rangePixelSpacing, p.prf,
                p.sensingStart, p.wavelength);
    });
    std::vector<float2> flat64Gpu(outN);
    CUDA_CHECK(cudaMemcpy(flat64Gpu.data(), dGeo, outN * sizeof(float2),
            cudaMemcpyDeviceToHost));

    const float flatten32GpuMs = timeFlatten([&]() {
        flattenKernelF32<<<grid, block>>>(dGeo, dRg, dAz, p.inRows, p.inCols,
                outN, (float)p.startingRange, (float)p.rangePixelSpacing,
                (float)p.prf, (float)p.sensingStart, (float)p.wavelength);
    });
    std::vector<float2> flat32Gpu(outN);
    CUDA_CHECK(cudaMemcpy(flat32Gpu.data(), dGeo, outN * sizeof(float2),
            cudaMemcpyDeviceToHost));

    // --- validation --------------------------------------------------------
    // interp: GPU vs CPU relative error (sincosf + reordering tolerance)
    double interpMaxRel = 0.0;
    for (size_t k = 0; k < outN; ++k) {
        if (!valid[k])
            continue;
        const double dx = (double)geoGpu[k].x - geoCpu[k].x;
        const double dy = (double)geoGpu[k].y - geoCpu[k].y;
        const double mag = std::hypot((double)geoCpu[k].x, (double)geoCpu[k].y);
        if (mag > 1e-12)
            interpMaxRel =
                    std::max(interpMaxRel, std::hypot(dx, dy) / mag);
    }

    // flatten fp64: GPU vs CPU applied-rotation phase difference
    double flat64MaxPhase = 0.0;
    for (size_t k = 0; k < outN; ++k) {
        if (!valid[k])
            continue;
        const std::complex<double> g(flat64Gpu[k].x, flat64Gpu[k].y);
        const std::complex<double> c(geoFlatCpu[k].x, geoFlatCpu[k].y);
        if (std::abs(c) > 1e-12)
            flat64MaxPhase =
                    std::max(flat64MaxPhase, std::abs(std::arg(g * std::conj(c))));
    }

    // fp32 vs fp64: analytic unwrapped phase error (host, per real formula)
    // + wrapped applied-rotation error from the GPU outputs.
    double fp32UnwrappedMax = 0.0, fp32UnwrappedSum = 0.0;
    double fp32WrappedMax = 0.0, fp32WrappedSum = 0.0;
    for (size_t k = 0; k < outN; ++k) {
        if (!valid[k])
            continue;
        const double rgD = rgIdx[k], azD = azIdx[k];
        const double rng = p.startingRange + rgD * p.rangePixelSpacing;
        const double az = p.sensingStart + azD / p.prf;
        const double ph64 = carrierPhase(az, rng, p.startingRange) +
                4.0 * (M_PI / p.wavelength) * rng;
        const float rngF = (float)p.startingRange +
                (float)rgD * (float)p.rangePixelSpacing;
        const float azF =
                (float)p.sensingStart + (float)azD / (float)p.prf;
        const float ph32 = carrierPhaseF(azF, rngF, (float)p.startingRange) +
                4.0f * ((float)M_PI / (float)p.wavelength) * rngF;
        const double unwrapped = std::abs((double)ph32 - ph64);
        fp32UnwrappedMax = std::max(fp32UnwrappedMax, unwrapped);
        fp32UnwrappedSum += unwrapped;

        const std::complex<double> g32(flat32Gpu[k].x, flat32Gpu[k].y);
        const std::complex<double> g64(flat64Gpu[k].x, flat64Gpu[k].y);
        if (std::abs(g64) > 1e-12) {
            const double w = std::abs(std::arg(g32 * std::conj(g64)));
            fp32WrappedMax = std::max(fp32WrappedMax, w);
            fp32WrappedSum += w;
        }
    }
    const double fp32UnwrappedMean =
            validN ? fp32UnwrappedSum / validN : 0.0;
    const double fp32WrappedMean = validN ? fp32WrappedSum / validN : 0.0;

    // --- report ------------------------------------------------------------
    const double h2dGBs = h2dBytes / (h2dMs * 1e-3) / 1e9;
    const double d2hGBs = d2hBytes / (d2hMs * 1e-3) / 1e9;
    const double e2eGpuMs =
            h2dMs + interpGpuMs + flatten64GpuMs + d2hMs;

    std::printf("\n-- timings (best of %d GPU / %d CPU reps) --\n", p.gpuReps,
            p.cpuReps);
    std::printf("interp   : CPU %9.3f ms | GPU %8.3f ms | speedup %6.1fx\n",
            interpCpuMs, interpGpuMs, interpCpuMs / interpGpuMs);
    std::printf("flatten64: CPU %9.3f ms | GPU %8.3f ms | speedup %6.1fx\n",
            flattenCpuMs, flatten64GpuMs, flattenCpuMs / flatten64GpuMs);
    std::printf("flatten32:                  GPU %8.3f ms | fp64/fp32 %5.2fx\n",
            flatten32GpuMs, flatten64GpuMs / flatten32GpuMs);
    std::printf("H2D: %.1f MiB in %.3f ms (%.1f GB/s) | D2H: %.1f MiB in "
                "%.3f ms (%.1f GB/s)\n",
            h2dBytes / 1048576.0, h2dMs, h2dGBs, d2hBytes / 1048576.0, d2hMs,
            d2hGBs);
    std::printf("GPU end-to-end (H2D + interp + flatten64 + D2H): %.3f ms\n",
            e2eGpuMs);

    std::printf("\n-- validation --\n");
    std::printf("interp GPU-vs-CPU max rel err     : %.3e\n", interpMaxRel);
    std::printf("flatten64 GPU-vs-CPU max phase err: %.3e rad\n",
            flat64MaxPhase);
    std::printf("fp32 flatten phase err (unwrapped): max %.3e rad, mean "
                "%.3e rad\n",
            fp32UnwrappedMax, fp32UnwrappedMean);
    std::printf("fp32 flatten phase err (wrapped)  : max %.3e rad, mean "
                "%.3e rad\n",
            fp32WrappedMax, fp32WrappedMean);

    bool pass = interpMaxRel < 1e-3 && flat64MaxPhase < 1e-5;
    std::printf("\nRESULT: %s\n", pass ? "PASS" : "FAIL");

    if (p.csv) {
        FILE* f = std::fopen(p.csv, "a");
        if (!f) {
            std::fprintf(stderr, "cannot open csv %s\n", p.csv);
        } else {
            if (std::ftell(f) == 0)
                std::fprintf(f,
                        "gpu,in_rows,in_cols,out_rows,out_cols,valid_px,"
                        "cpu_threads,interp_cpu_ms,interp_gpu_ms,"
                        "flatten_cpu_ms,flatten64_gpu_ms,flatten32_gpu_ms,"
                        "h2d_ms,h2d_bytes,d2h_ms,d2h_bytes,e2e_gpu_ms,"
                        "interp_max_rel,flat64_max_phase,"
                        "fp32_unwrapped_max_rad,fp32_unwrapped_mean_rad,"
                        "ws_bbox_mib,ws_rowspan_mib\n");
            std::fprintf(f,
                    "%s,%d,%d,%d,%d,%zu,%d,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,"
                    "%zu,%.4f,%zu,%.4f,%.4e,%.4e,%.4e,%.4e,%.2f,%.2f\n",
                    prop.name, p.inRows, p.inCols, p.outRows, p.outCols,
                    validN, omp_get_max_threads(), interpCpuMs, interpGpuMs,
                    flattenCpuMs, flatten64GpuMs, flatten32GpuMs, h2dMs,
                    h2dBytes, d2hMs, d2hBytes, e2eGpuMs, interpMaxRel,
                    flat64MaxPhase, fp32UnwrappedMax, fp32UnwrappedMean,
                    wsBboxMiB, wsRowsMiB);
            std::fclose(f);
        }
    }

    CUDA_CHECK(cudaFree(dRdr));
    CUDA_CHECK(cudaFree(dGeo));
    CUDA_CHECK(cudaFree(dGeoIn));
    CUDA_CHECK(cudaFree(dRg));
    CUDA_CHECK(cudaFree(dAz));
    CUDA_CHECK(cudaFree(dTable));
    return pass ? 0 : 1;
}
