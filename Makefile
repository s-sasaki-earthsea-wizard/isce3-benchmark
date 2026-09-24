SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -eu -c
.ONESHELL:
.DEFAULT_GOAL := help

# Load .env if present so `make` invocations can read host paths.
ifneq (,$(wildcard ./.env))
include .env
export
endif

COMPOSE ?= docker compose
RUN     := $(COMPOSE) run --rm dev

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?##"}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- environment --------------------------------------------------------------
.PHONY: env-check
env-check: ## Sanity-check host env (Docker, NVIDIA runtime, .env)
	@command -v docker >/dev/null || { echo "docker not found"; exit 1; }
	@docker info 2>/dev/null | grep -qi nvidia || \
	  { echo "WARN: NVIDIA Container Toolkit runtime not detected"; }
	@test -f .env || { echo "missing .env (cp .env.example .env)"; exit 1; }
	@test -d "$$ISCE3_SRC" || { echo "ISCE3_SRC=$$ISCE3_SRC does not exist"; exit 1; }
	@echo "env OK"

.PHONY: build
build: ## Build the dev container image
	$(COMPOSE) build dev

.PHONY: shell
shell: ## Interactive shell inside the container
	$(RUN) bash

# --- isce3 build --------------------------------------------------------------
.PHONY: isce3
isce3: ## Build isce3 from the bind-mounted source tree (CUDA enabled)
	$(RUN) bash scripts/build_isce3.sh

.PHONY: isce3-clean
isce3-clean: ## Wipe the persistent isce3 build directory
	rm -rf $(ISCE3_BUILD_DIR)

# --- data ---------------------------------------------------------------------
.PHONY: data-ree
data-ree: ## Stage REE synthetic test fixtures into ./data/REE
	bash fetch/fetch_ree.sh

.PHONY: data-s1
data-s1: ## Fetch POEORB orbits + DEM for the Boso S1 SAFE pair into ./data/S1-boso
	$(RUN) python fetch/fetch_sentinel1.py \
	    --safe /data/S1-data/S1A_IW_SLC__1SDV_20251221T204341_20251221T204408_062418_07D1B4_CC6C.SAFE \
	    --safe /data/S1-data/S1A_IW_SLC__1SDV_20260126T204338_20260126T204405_062943_07E587_C319.SAFE \
	    --out  /data/S1-boso

.PHONY: data-s1-burst-db
data-s1-burst-db: ## Build minimal COMPASS burst DB for the chosen Boso burst (geo-mode profile)
	$(RUN) python fetch/build_burst_db.py \
	    --bursts /data/S1-boso/bursts.json \
	    --burst-id t046_097519_iw3 \
	    --pol VV \
	    --out /data/S1-boso/burst_db.sqlite3

.PHONY: render-s1
render-s1: ## Render concrete S1 CSLC runconfigs from templates (after data-s1)
	$(RUN) python tools/render_s1_runconfig.py \
	    --bursts /data/S1-boso/bursts.json \
	    --orbits-dir /data/S1-boso/orbits \
	    --dem /data/S1-boso/dem.tif

# --- ALOS-2 Kujukuri (L-band closure network) ---------------------------------
.PHONY: data-alos2
data-alos2: ## Download the JAXA ALOS-2 Kujukuri L1.1 sample stack (12 scenes, ~73 GB) into ./data/ALOS2-kujukuri
	bash fetch/fetch_alos2_kujukuri.sh --set asc

.PHONY: alos2-convert
alos2-convert: ## Convert the ALOS-2 CEOS zips to NISAR RSLC HDF5 (isce3 v0.25.16 build) into ./data/ALOS2-kujukuri/rslc
	bash scripts/convert_alos2_kujukuri.sh

.PHONY: alos2-network
alos2-network: ## Generate all C(12,2)=66 pair runconfigs + closure manifest for the Kujukuri stack
	python3 tools/make_alos2_network.py --rslc-dir data/ALOS2-kujukuri/rslc \
	    --template configs/insar_alos2_kujukuri_template.yaml \
	    --out-dir configs/alos2_kujukuri \
	    --gunw-root $(CURDIR)/data/ALOS2-kujukuri/gunw \
	    --manifest configs/alos2_kujukuri/pairs_ALOS2_kujukuri.json --force

.PHONY: alos2-run
alos2-run: ## Run the ALOS-2 pair network sequentially on the GPU (scripts/run_alos2_network.sh: --only, --max-pairs)
	bash scripts/run_alos2_network.sh --keep-going

# --- Capella SICD (Stage U0: RGZERO SICD -> NISAR RSLC, bench#54) --------------
.PHONY: data-capella
data-capella: ## Download the Capella Mexico City stripmap SICD pair (1.6 GB, anonymous HTTPS) into ./data/capella_mexico_city
	bash fetch/fetch_capella_mexico_city.sh --set pair

.PHONY: capella-dem
capella-dem: ## Stitch a Copernicus GLO-30 DEM over Mexico City into ./data/capella_mexico_city/dem.tif
	$(RUN) python fetch/fetch_dem_bbox.py --bbox -99.25 19.10 -98.75 19.60 \
	    --out /data/capella_mexico_city/dem.tif

.PHONY: capella-convert
capella-convert: ## Convert both Capella SICDs to NISAR RSLC HDF5 (tools/sicd_to_nisar_rslc.py) into ./data/capella_mexico_city/rslc
	mkdir -p data/capella_mexico_city/rslc data/capella_mexico_city/logs
	$(RUN) bash -c 'for s in 20240626150051_20240626150055 20240629134910_20240629134915; do \
	    python tools/sicd_to_nisar_rslc.py /data/capella_mexico_city/CAPELLA_C14_SM_SICD_HH_$$s.ntf \
	        /data/capella_mexico_city/rslc/$${s:0:8}.h5 --overwrite \
	        > /data/capella_mexico_city/logs/convert_$${s:0:8}.log 2>&1 \
	        || { echo "FAIL convert $${s:0:8}" >&2; exit 1; }; echo "CONVERT-OK $${s:0:8}"; done'

.PHONY: capella-geometry-check
capella-geometry-check: ## isce3 half of the RSLC geometry round-trip (the sarkit half runs on the host, see the report)
	$(RUN) bash -c 'for d in 20240626 20240629; do \
	    python tools/sicd_rslc_geometry_check.py isce3 /data/capella_mexico_city/rslc/$$d.h5 \
	        --out /data/capella_mexico_city/rslc/geom_isce3_$$d.json \
	        || { echo "FAIL geometry-check $$d" >&2; exit 1; }; done'

.PHONY: capella-rifg
capella-rifg: ## Run insar.py to RIFG on the Capella pair, crossmul flatten on and off (scripts/run_capella_pair.sh)
	bash scripts/run_capella_pair.sh flat noflat

.PHONY: capella-convert-beta0
capella-convert-beta0: ## Convert both Capella SICDs to beta0-calibrated RSLCs for GCOV into ./data/capella_mexico_city/rslc_beta0
	$(RUN) bash scripts/capella_rtc_steps.sh convert-beta0

# --- benchmarks ---------------------------------------------------------------
.PHONY: dry-run
dry-run: ## Validate every config (schema + loader + input existence). Fast gate.
	$(RUN) bash scripts/dry_run.sh

.PHONY: smoke
smoke: dry-run ## Tiny end-to-end smoke run on REE (CPU+GPU). Runs dry-run first.
	$(RUN) bash scripts/run_bench.sh smoke

.PHONY: smoke-s1
smoke-s1: ## Sentinel-1 Boso bench (CSLC ref+sec + crossmul, CPU+GPU, repeats=1)
	$(RUN) bash scripts/run_bench.sh s1

.PHONY: bench
bench: ## Full bench sweep defined in scripts/run_bench.sh
	$(RUN) bash scripts/run_bench.sh full

.PHONY: poc-geocode-slc
poc-geocode-slc: ## Build + run the geocode_slc CUDA PoC microbenchmark (issue #11)
	$(RUN) bash poc/geocode_slc/run.sh

# --- NISAR Stage 2a (issue #18) ----------------------------------------------
# Expects the RSLC + official GCOV reference under ./data/NISAR/{RSLC,GCOV}
# (staged manually; ~18 GB, not fetched by the harness). Granule names for
# NISAR_ANC below come from the official GCOV's embedded runconfig.

NISAR_REF_GCOV := /data/NISAR/GCOV/NISAR_L2_PR_GCOV_025_125_A_017_4005_DHDH_A_20260716T203701_20260716T203721_P05023_N_P_J_001.h5

.PHONY: data-nisar-anc
data-nisar-anc: ## Fetch TEC + MOE orbit ancillaries from ASF DAAC (~/.netrc auth) into ./data/NISAR/ancillary
	python3 fetch/fetch_nisar_ancillary.py --out data/NISAR/ancillary \
	    NISAR_ANC_TEC_20260717T211944_20260715T230002_20260716T235952_s015 \
	    NISAR_ANC_J_PR_MOE_20260717T132621_20260715T205942_20260717T025942

.PHONY: gcov-freqB
gcov-freqB: ## NISAR GCOV freqB (80 m) smoke run (CPU, ~3 min)
	$(RUN) bash -c "mkdir -p /data/NISAR/out_gcov_freqB && cd /data/NISAR/out_gcov_freqB && /usr/bin/time -v python /work/scripts/run_gcov_nisar.py /work/configs/nisar_gcov_kyushu_freqB_cpu.yaml"

.PHONY: gcov-freqA
gcov-freqA: ## NISAR GCOV freqA (10 m) baseline run (CPU, ~30 min, ~59 GB RAM)
	$(RUN) bash -c "mkdir -p /data/NISAR/out_gcov_freqA && cd /data/NISAR/out_gcov_freqA && /usr/bin/time -v python /work/scripts/run_gcov_nisar.py /work/configs/nisar_gcov_kyushu_freqA_cpu.yaml"

.PHONY: gcov-freqA-anc
gcov-freqA-anc: ## NISAR GCOV freqA same-ancillary rerun (official TEC + MOE orbit; needs data-nisar-anc)
	$(RUN) bash -c "mkdir -p /data/NISAR/out_gcov_freqA_anc && cd /data/NISAR/out_gcov_freqA_anc && /usr/bin/time -v python /work/scripts/run_gcov_nisar.py /work/configs/nisar_gcov_kyushu_freqA_anc_cpu.yaml"

.PHONY: compare-gcov
compare-gcov: ## Compare all local GCOV outputs against the official reference
	$(RUN) bash -c "python /work/scripts/compare_gcov_nisar.py --ours /data/NISAR/out_gcov_freqB/gcov_freqB.h5 --ref $(NISAR_REF_GCOV) --freq B; \
	    for d in out_gcov_freqA out_gcov_freqA_anc; do \
	        [ -d /data/NISAR/$$d ] && python /work/scripts/compare_gcov_nisar.py --ours /data/NISAR/$$d --ref $(NISAR_REF_GCOV) --freq A; \
	    done; true"

.PHONY: profile-nsys
profile-nsys: ## Nsight Systems trace on a single workflow run
	$(RUN) bash scripts/run_profile_nsys.sh

.PHONY: profile-pyspy
profile-pyspy: ## py-spy sampling profile on a single workflow run
	$(RUN) bash scripts/run_profile_pyspy.sh

.PHONY: profile-pyspy-gcov-freqB
profile-pyspy-gcov-freqB: ## py-spy profile of NISAR GCOV freqB smoke (~3 min, separate output dir)
	$(RUN) bash scripts/run_profile_pyspy.sh /work/configs/nisar_gcov_kyushu_freqB_profile_cpu.yaml

.PHONY: profile-pyspy-gcov-freqA
profile-pyspy-gcov-freqA: ## py-spy profile of NISAR GCOV freqA (~30 min, ~59 GB RAM, separate output dir)
	$(RUN) bash scripts/run_profile_pyspy.sh /work/configs/nisar_gcov_kyushu_freqA_profile_cpu.yaml

# --- analysis -----------------------------------------------------------------
.PHONY: report
report: ## Aggregate latest log dir into a markdown report
	python tools/parse_timing.py --logs $(BENCH_LOG_DIR) --out reports/

.PHONY: clean
clean: ## Remove local logs (data/ untouched)
	rm -rf logs_*/
