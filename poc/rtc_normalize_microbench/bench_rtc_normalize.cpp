// Microbenchmark for isce3 RTC _normalizeRtcArea loop variants.
//
// Context: isce-framework/isce3#341 reports that the develop-branch loop
// (per-pixel dynamic scheduling + per-pixel atomics) costs ~11% of GCOV
// geocode time at NISAR scale. The fix proposed in the issue is a plain
// row-wise `omp parallel for` with a scalar conditional. Since
// isce3::core::Matrix<T> derives from Eigen::Array, an Eigen
// expression-based variant is also idiomatic. This benchmark isolates the
// normalize pass and compares four implementations on identical inputs:
//
//   v0_develop      : `omp parallel for schedule(dynamic) collapse(2)` +
//                     per-pixel `omp atomic` (develop @ bdf1f6f)
//   v1_plain_omp    : row-wise `omp parallel for`, scalar ternary
//                     (the fix as filed in #341)
//   v2_omp_eigen    : row-wise `omp parallel for`, per-row Eigen
//                     `select()` expression (vectorizable, branch-free)
//   v3_eigen_whole  : whole-array Eigen `select()`, no OpenMP
//                     (what a "naive Eigen" rewrite would do — Eigen does
//                     not thread coefficient-wise expressions)
//
// All variants must produce bit-identical output; this is asserted
// against the v0 result. Inter-rep restores of the in/out array are done
// with a parallel row-wise memcpy and timed, giving a multi-threaded
// memory-bandwidth reference for the "this pass is bandwidth-bound"
// argument.
//
// Build/run: see build.sh / run.sh in this directory (container-side,
// same GCC 13.3 + -O2 -g -DNDEBUG as the isce3 RelWithDebInfo build used
// for the #341 measurements).

#include <isce3/core/Matrix.h>

#include <omp.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

using Matrix = isce3::core::Matrix<float>;
using EArr = Eigen::Array<float, Eigen::Dynamic, Eigen::Dynamic, Eigen::RowMajor>;

namespace {

// Deterministic per-index hash (splitmix64 finalizer) so initialization is
// parallel, reproducible, and independent of iteration order.
inline std::uint64_t hash64(std::uint64_t x)
{
    x += 0x9e3779b97f4a7c15ULL;
    x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
    x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
    return x ^ (x >> 31);
}

void initInputs(Matrix& num, Matrix& den, double zero_fraction)
{
    const std::int64_t nrows = num.rows();
    const std::int64_t ncols = num.cols();
    const auto zero_threshold = static_cast<std::uint64_t>(
            zero_fraction * static_cast<double>(
                    std::numeric_limits<std::uint32_t>::max()));
#pragma omp parallel for
    for (std::int64_t i = 0; i < nrows; ++i) {
        for (std::int64_t j = 0; j < ncols; ++j) {
            const std::uint64_t idx =
                    static_cast<std::uint64_t>(i) * ncols + j;
            const std::uint64_t hn = hash64(idx);
            const std::uint64_t hd = hash64(~idx);
            // numerator: positive float in [0.5, 1.5)
            num(i, j) = 0.5f + static_cast<float>(hn & 0xffffff) / 0x1000000p0f;
            // denominator: exact zero with probability zero_fraction,
            // else in [0.5, 2.5)
            const bool is_zero =
                    static_cast<std::uint32_t>(hd) < zero_threshold;
            den(i, j) = is_zero ? 0.0f
                                : 0.5f + 2.0f * static_cast<float>(
                                                 (hd >> 32) & 0xffffff) /
                                                 0x1000000p0f;
        }
    }
}

// Parallel row-wise copy; returns elapsed seconds. Doubles as the restore
// between reps and as a memory-bandwidth probe (1 read + 1 write stream).
double restore(Matrix& dst, const Matrix& src)
{
    const std::int64_t nrows = dst.rows();
    const std::size_t row_bytes = dst.cols() * sizeof(float);
    const double t0 = omp_get_wtime();
#pragma omp parallel for
    for (std::int64_t i = 0; i < nrows; ++i)
        std::memcpy(dst.data() + i * dst.cols(),
                src.data() + i * src.cols(), row_bytes);
    return omp_get_wtime() - t0;
}

// v0: verbatim replica of develop @ bdf1f6f _normalizeRtcArea
// (cxx/isce3/geometry/RTC.cpp#L268-L286), including loop-variable types.
void v0_develop(Matrix& numerator_array, const Matrix& denominator_array)
{
    _Pragma("omp parallel for schedule(dynamic) collapse(2)")
        for (int i = 0; i < numerator_array.length(); ++i)
            for (int j = 0; j < numerator_array.width(); ++j) {
                const float denominator_value = denominator_array(i, j);
                if (denominator_value == 0) {
                    _Pragma("omp atomic write")
                        numerator_array(i, j) =
                                std::numeric_limits<float>::quiet_NaN();
                    continue;
                }
                _Pragma("omp atomic update")
                    numerator_array(i, j) /= denominator_value;
            }
}

// v1: the fix as filed in #341 (and on fork branch
// perf/rtc-normalize-schedule).
void v1_plain_omp(Matrix& numerator_array, const Matrix& denominator_array)
{
    _Pragma("omp parallel for")
        for (int i = 0; i < numerator_array.length(); ++i)
            for (int j = 0; j < numerator_array.width(); ++j) {
                const float denominator_value = denominator_array(i, j);
                numerator_array(i, j) = denominator_value == 0
                        ? std::numeric_limits<float>::quiet_NaN()
                        : numerator_array(i, j) / denominator_value;
            }
}

// v2: same row-wise OpenMP threading, per-row Eigen select() expression.
// Matrix is RowMajor so each row is contiguous; select() is branch-free
// and eligible for vectorization. The unconditional division may raise
// FE_DIVBYZERO/FE_INVALID on masked lanes (values are discarded).
void v2_omp_eigen(Matrix& numerator_array, const Matrix& denominator_array)
{
    const std::int64_t nrows = numerator_array.rows();
    _Pragma("omp parallel for")
        for (std::int64_t i = 0; i < nrows; ++i) {
            auto num_row = numerator_array.row(i);
            const auto den_row = denominator_array.row(i);
            num_row = (den_row == 0.0f)
                              .select(std::numeric_limits<
                                              float>::quiet_NaN(),
                                      num_row / den_row);
        }
}

// v3: whole-array Eigen expression, no explicit threading. Eigen does not
// parallelize coefficient-wise expressions (only GEMM-family products),
// so this runs single-threaded.
void v3_eigen_whole(Matrix& numerator_array, const Matrix& denominator_array)
{
    static_cast<EArr&>(numerator_array) =
            (static_cast<const EArr&>(denominator_array) == 0.0f)
                    .select(std::numeric_limits<float>::quiet_NaN(),
                            static_cast<EArr&>(numerator_array) /
                                    static_cast<const EArr&>(
                                            denominator_array));
}

double median(std::vector<double> v)
{
    std::sort(v.begin(), v.end());
    const std::size_t n = v.size();
    return n % 2 ? v[n / 2] : 0.5 * (v[n / 2 - 1] + v[n / 2]);
}

} // namespace

int main(int argc, char* argv[])
{
    std::int64_t nrows = 29240; // NISAR L1 RSLC freq A radar grid (#341)
    std::int64_t ncols = 21232;
    int reps_slow = 3; // v0 (expected ~1e2 s/rep at full size)
    int reps_fast = 5; // v1/v2/v3
    if (argc > 2) {
        nrows = std::stoll(argv[1]);
        ncols = std::stoll(argv[2]);
    }
    if (argc > 4) {
        reps_slow = std::stoi(argv[3]);
        reps_fast = std::stoi(argv[4]);
    }

    const double gb = static_cast<double>(nrows) * ncols * sizeof(float) / 1e9;
    std::printf("grid: %lld x %lld (%.2f GB/array), threads: %d\n",
            static_cast<long long>(nrows), static_cast<long long>(ncols),
            gb, omp_get_max_threads());
    std::printf("reps: v0=%d, v1/v2/v3=%d\n\n", reps_slow, reps_fast);

    Matrix num(nrows, ncols);
    Matrix den(nrows, ncols);
    Matrix pristine(nrows, ncols);
    Matrix reference(nrows, ncols);
    initInputs(num, den, /*zero_fraction=*/0.05);
    restore(pristine, num);

    // Reference result from v0 (develop) for bit-identity checks.
    v0_develop(num, den);
    restore(reference, num);

    const std::size_t nbytes =
            static_cast<std::size_t>(nrows) * ncols * sizeof(float);

    struct Variant {
        const char* name;
        void (*fn)(Matrix&, const Matrix&);
        int reps;
    };
    const Variant variants[] = {
            {"v0_develop (dynamic+atomic)", v0_develop, reps_slow},
            {"v1_plain_omp (#341 fix)", v1_plain_omp, reps_fast},
            {"v2_omp_eigen (rows+select)", v2_omp_eigen, reps_fast},
            {"v3_eigen_whole (1 thread)", v3_eigen_whole, reps_fast},
    };

    std::vector<double> restore_times;
    for (const auto& v : variants) {
        std::vector<double> times;
        bool identical = true;
        for (int r = 0; r < v.reps; ++r) {
            restore_times.push_back(restore(num, pristine));
            const double t0 = omp_get_wtime();
            v.fn(num, den);
            const double t1 = omp_get_wtime();
            times.push_back(t1 - t0);
            if (r == 0)
                identical =
                        std::memcmp(num.data(), reference.data(), nbytes) == 0;
        }
        std::printf("%-30s median %9.3f s  (", v.name, median(times));
        for (std::size_t k = 0; k < times.size(); ++k)
            std::printf("%s%.3f", k ? ", " : "", times[k]);
        std::printf(")  bit-identical vs v0: %s\n",
                identical ? "YES" : "NO  <-- MISMATCH");
    }

    // 1 read + 1 write stream over the array pair.
    const double med_restore = median(restore_times);
    std::printf("\nparallel memcpy restore: median %.3f s -> %.1f GB/s "
                "effective (2x%.2f GB moved)\n",
            med_restore, 2.0 * gb / med_restore, gb);
    return 0;
}
