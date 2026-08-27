/* bench#36 Step 2 — FFTW_MEASURE plan-selection AND result-stability probe.
 *
 * Replicates, in isolation, the FFTW plans that isce3's CPU Ampcor
 * (cxx/isce3/matchtemplate/pycuampcor/cuCorrFrequency.cpp) builds with
 * FFTW_MEASURE and no wisdom.  For the runconfig used by the bench#36 CPU
 * E2E (window 64x96, half-search 32/32, SLC oversampling 2, batch 10x1):
 *
 *   raw correlator        : n = {160, 128}, howmany = 10
 *   oversampled correlator: n = {208, 144}, howmany = 10
 *
 * For each transform it prints
 *   plan_hash  -- FNV-1a of fftwf_sprint_plan(), i.e. which algorithm FFTW
 *                 chose; and
 *   out_hash   -- FNV-1a of the raw output bytes for a FIXED, deterministic
 *                 input (a seeded LCG, identical in every run).
 *
 * plan_hash varying across runs shows the timing-based planner picking
 * different algorithms.  out_hash varying is the part that matters: it means
 * that choice changes the floating-point result for identical input, which is
 * a mechanism for run-to-run ULP noise in dense_offsets output.  plan_hash
 * varying while out_hash stays constant would mean the plan differences are
 * numerically inert.
 *
 * NOTE: FFTW_MEASURE overwrites the arrays while planning, so the input is
 * filled only AFTER both plans are created.
 *
 * Build (inside the dev container):
 *   gcc -O2 -I$CONDA_PREFIX/include -o fftw_plan_probe fftw_plan_probe.c \
 *       -L$CONDA_PREFIX/lib -lfftw3f -lm
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <fftw3.h>

static unsigned long long fnv1a_bytes(const void *p, size_t n)
{
    const unsigned char *b = (const unsigned char *)p;
    unsigned long long h = 1469598103934665603ULL;
    for (size_t i = 0; i < n; ++i) {
        h ^= b[i];
        h *= 1099511628211ULL;
    }
    return h;
}

static unsigned long long fnv1a_str(const char *s)
{
    return fnv1a_bytes(s, strlen(s));
}

/* Deterministic input, identical in every run and every arm. */
static void fill(float *a, size_t n)
{
    uint64_t s = 88172645463325252ULL;
    for (size_t i = 0; i < n; ++i) {
        s ^= s << 13; s ^= s >> 7; s ^= s << 17;
        a[i] = (float)((double)(s >> 11) / 9007199254740992.0) - 0.5f;
    }
}

static const char *g_outdir = NULL;
static int g_run = 0;

static void dump(const char *label, const char *kind, const void *p, size_t nbytes)
{
    if (!g_outdir) return;
    char path[512];
    snprintf(path, sizeof(path), "%s/run%d.%s_%s.bin", g_outdir, g_run, label, kind);
    FILE *f = fopen(path, "wb");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return; }
    fwrite(p, 1, nbytes, f);
    fclose(f);
}

static void probe(const char *label, int nx, int ny, int howmany)
{
    const int rank = 2;
    int n[2] = {nx, ny};
    const size_t image_size = (size_t)nx * ny;
    const size_t fimage_size = (size_t)nx * (ny / 2 + 1);
    const size_t nreal = image_size * howmany;
    const size_t ncplx = fimage_size * howmany;

    float *in = fftwf_alloc_real(nreal);
    fftwf_complex *out = fftwf_alloc_complex(ncplx);
    if (!in || !out) { fprintf(stderr, "alloc failed\n"); exit(1); }

    fftwf_plan fwd = fftwf_plan_many_dft_r2c(rank, n, howmany,
            in, NULL, 1, (int)image_size,
            out, NULL, 1, (int)fimage_size, FFTW_MEASURE);
    fftwf_plan bwd = fftwf_plan_many_dft_c2r(rank, n, howmany,
            out, NULL, 1, (int)fimage_size,
            in, NULL, 1, (int)image_size, FFTW_MEASURE);

    char *sf = fftwf_sprint_plan(fwd);
    char *sb = fftwf_sprint_plan(bwd);

    /* Planning is done; only now install the fixed input. */
    fill(in, nreal);
    const unsigned long long in_hash = fnv1a_bytes(in, nreal * sizeof(float));

    fftwf_execute(fwd);
    const unsigned long long fwd_out = fnv1a_bytes(out, ncplx * sizeof(fftwf_complex));
    dump(label, "r2c", out, ncplx * sizeof(fftwf_complex));

    fftwf_execute(bwd);            /* round trip, unnormalised */
    const unsigned long long bwd_out = fnv1a_bytes(in, nreal * sizeof(float));

    printf("%s_r2c plan_hash=%016llx plan_len=%zu in_hash=%016llx out_hash=%016llx\n",
           label, fnv1a_str(sf), strlen(sf), in_hash, fwd_out);
    printf("%s_c2r plan_hash=%016llx plan_len=%zu out_hash=%016llx\n",
           label, fnv1a_str(sb), strlen(sb), bwd_out);

    free(sf); free(sb);
    fftwf_destroy_plan(fwd); fftwf_destroy_plan(bwd);
    fftwf_free(in); fftwf_free(out);
}

int main(int argc, char **argv)
{
    /* argv[1] = run index, argv[2] = directory to dump raw outputs into. */
    if (argc > 1) g_run = atoi(argv[1]);
    if (argc > 2) g_outdir = argv[2];

    /* No wisdom import -- exactly like isce3's Ampcor. */
    probe("raw", 160, 128, 10);
    probe("oversampled", 208, 144, 10);
    return 0;
}
