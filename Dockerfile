# =============================================================================
# isce3-benchmark dev image
#
# Base: NVIDIA CUDA 12.8 devel on Ubuntu 24.04
#   - CUDA 12.8 is the first toolkit with first-class sm_120 (Blackwell) support
#   - Ubuntu 24.04 matches the host (kernel 6.17), so libstdc++ is current
#
# Build strategy: install isce3 build dependencies via micromamba (matches the
# upstream environment.yml), then build isce3 from a bind-mounted source tree
# at runtime via scripts/build_isce3.sh. We do NOT bake a pinned isce3 commit
# into the image — that lets us rebuild against PR branches by remounting.
# =============================================================================
ARG CUDA_VERSION=12.8.1
ARG UBUNTU_VERSION=24.04
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${UBUNTU_VERSION}

ARG MAMBA_VERSION=2.0.5-0
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    MAMBA_ROOT_PREFIX=/opt/micromamba \
    PATH=/opt/micromamba/bin:/opt/micromamba/envs/isce3/bin:/usr/local/cuda/bin:${PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        ccache \
        cuda-nsight-systems-12-8 \
        curl \
        git \
        less \
        ninja-build \
        pkg-config \
        time \
        vim-tiny \
    && rm -rf /var/lib/apt/lists/*

# --- micromamba ---------------------------------------------------------------
# Pull the static binary from the official GitHub releases. The legacy
# micro.mamba.pm tarball API has been returning corrupted streams as of 2026-05.
RUN mkdir -p ${MAMBA_ROOT_PREFIX}/bin \
 && curl -fsSL -o ${MAMBA_ROOT_PREFIX}/bin/micromamba \
        "https://github.com/mamba-org/micromamba-releases/releases/download/${MAMBA_VERSION}/micromamba-linux-64" \
 && chmod +x ${MAMBA_ROOT_PREFIX}/bin/micromamba

# --- isce3 build dependencies (mirror upstream environment.yml) ---------------
# We install profiling tooling here too: py-spy, pytest-benchmark.
COPY docker/env-isce3-build.yml /tmp/env-isce3-build.yml
RUN micromamba env create -y -n isce3 -f /tmp/env-isce3-build.yml \
 && micromamba clean -ay \
 && rm /tmp/env-isce3-build.yml

# --- entrypoint ---------------------------------------------------------------
# /opt/isce3-src    : bind-mount of the host isce3 source tree (read-only)
# /opt/isce3-build  : bind-mount of host-persisted build dir (read-write)
# /data             : benchmark input data
# /logs             : benchmark output (timing, nsys traces, py-spy)
# /work             : isce3-benchmark project root
WORKDIR /work
COPY docker/entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh
ENTRYPOINT ["/opt/entrypoint.sh"]
CMD ["bash"]
