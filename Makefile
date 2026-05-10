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

# --- benchmarks ---------------------------------------------------------------
.PHONY: dry-run
dry-run: ## Validate every config (schema + loader + input existence). Fast gate.
	$(RUN) bash scripts/dry_run.sh

.PHONY: smoke
smoke: dry-run ## Tiny end-to-end smoke run on REE (CPU+GPU). Runs dry-run first.
	$(RUN) bash scripts/run_bench.sh smoke

.PHONY: bench
bench: ## Full bench sweep defined in scripts/run_bench.sh
	$(RUN) bash scripts/run_bench.sh full

.PHONY: profile-nsys
profile-nsys: ## Nsight Systems trace on a single workflow run
	$(RUN) bash scripts/run_profile_nsys.sh

.PHONY: profile-pyspy
profile-pyspy: ## py-spy sampling profile on a single workflow run
	$(RUN) bash scripts/run_profile_pyspy.sh

# --- analysis -----------------------------------------------------------------
.PHONY: report
report: ## Aggregate latest log dir into a markdown report
	python tools/parse_timing.py --logs $(BENCH_LOG_DIR) --out reports/

.PHONY: clean
clean: ## Remove local logs (data/ untouched)
	rm -rf logs_*/
