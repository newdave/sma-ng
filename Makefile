.PHONY: help install install-dev install-all lint lint-fix test test-cov clean daemon convert codecs preview detect-gpu config systemd-install \
        deploy deploy-check deploy-host remote-make

PYTHON ?= python3
VENV ?= venv
PIP = $(VENV)/bin/pip
PY = $(VENV)/bin/python

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

venv: ## Create virtual environment
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

install: venv ## Install base dependencies
	$(PIP) install -r setup/requirements.txt

install-dev: install ## Install dev dependencies (lint, test)
	$(PIP) install -e ".[dev]"

install-all: install ## Install all optional dependencies
	$(PIP) install -r setup/requirements-qbittorrent.txt
	$(PIP) install -r setup/requirements-deluge.txt
	$(PIP) install -e ".[dev]"

lint: ## Run linter (ruff)
	$(PY) -m ruff check .

lint-fix: ## Run linter with auto-fix
	$(PY) -m ruff check --fix .

test: ## Run tests
	$(PY) -m pytest tests/

test-cov: ## Run tests with coverage
	$(PY) -m pytest tests/ --cov=resources --cov=converter --cov-report=html

daemon: ## Start the daemon server
	$(PY) daemon.py --host 0.0.0.0 --port 8585

convert: ## Convert a file (usage: make convert FILE=/path/to/file.mkv)
	$(PY) manual.py -i "$(FILE)" -a

codecs: ## List supported codecs
	$(PY) manual.py -cl

preview: ## Preview conversion options (usage: make preview FILE=/path/to/file.mkv)
	$(PY) manual.py -i "$(FILE)" -oo

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ htmlcov/ .coverage .ruff_cache/

# ---------------------------------------------------------------------------
# GPU detection
# ---------------------------------------------------------------------------
# Detect GPU type: nvenc (NVIDIA), qsv (Intel), videotoolbox (Apple), vaapi (Linux Mesa/AMD), or software
# Use := for eager evaluation so detection runs at most once per make invocation
GPU ?= $(shell \
  if [ "$$(uname)" = "Darwin" ]; then \
    if sysctl -n machdep.cpu.brand_string 2>/dev/null | grep -qi apple; then \
      echo videotoolbox; \
    else \
      echo software; \
    fi; \
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then \
    echo nvenc; \
  elif [ -d /sys/module/i915 ] || (command -v vainfo >/dev/null 2>&1 && vainfo 2>&1 | grep -qi intel); then \
    echo qsv; \
  elif [ -e /dev/dri/renderD128 ] && (command -v vainfo >/dev/null 2>&1 && vainfo >/dev/null 2>&1); then \
    echo vaapi; \
  else \
    echo software; \
  fi \
)

detect-gpu: ## Detect GPU type for hardware acceleration
	@echo "$(GPU)"

config: ## Create config with GPU auto-detection (GPU=<type> to override)
	@mkdir -p config
	@if [ -f config/autoProcess.ini ]; then \
		echo "config/autoProcess.ini already exists, skipping (delete it first to regenerate)"; \
	else \
		cp setup/autoProcess.ini.sample config/autoProcess.ini; \
		if [ "$(GPU)" != "software" ]; then \
			sed -i.bak 's/^gpu *=.*/gpu = $(GPU)/' config/autoProcess.ini && rm -f config/autoProcess.ini.bak; \
			echo "Created config/autoProcess.ini (gpu = $(GPU))"; \
		else \
			echo "Created config/autoProcess.ini (software encoding)"; \
		fi; \
	fi
	@test -f config/daemon.json || (cp setup/daemon.json.sample config/daemon.json && echo "Created config/daemon.json")

systemd-install: ## Install systemd service (run as root)
	cp setup/sma-daemon.service /etc/systemd/system/
	systemctl daemon-reload
	@echo "Service installed. Enable with: systemctl enable --now sma-daemon"

# ---------------------------------------------------------------------------
# Deployment
#
# Copy .local.sample to .local (INI format) and fill in [deploy] DEPLOY_HOSTS.
# Per-host sections override [deploy] defaults for any key.
# See .local.sample for all available keys.
# ---------------------------------------------------------------------------

_LOCAL     = .local
_CFG       = scripts/local-config.sh

# Read DEPLOY_HOSTS from the [deploy] section at parse time so the loop in
# the deploy/remote-make recipes can iterate over it.
DEPLOY_HOSTS := $(shell [ -f $(_LOCAL) ] && $(_CFG) $(_LOCAL) deploy DEPLOY_HOSTS || true)

deploy-check: ## Verify .local exists and DEPLOY_HOSTS is set
	@if [ ! -f $(_LOCAL) ]; then \
		echo "ERROR: $(_LOCAL) not found. Copy .local.sample to .local and configure it."; \
		exit 1; \
	fi
	@if [ -z "$(DEPLOY_HOSTS)" ]; then \
		echo "ERROR: DEPLOY_HOSTS is not set in the [deploy] section of $(_LOCAL)."; \
		exit 1; \
	fi
	@echo "Deployment targets: $(DEPLOY_HOSTS)"

# Internal target — resolve per-host config, rsync, optional branch checkout,
# then run the on-host make target.  Called as: make deploy-host _HOST=user@host
deploy-host:
	@host="$(_HOST)"; \
	cfg="$(_CFG) $(_LOCAL) $$host"; \
	dir=$$($$cfg DEPLOY_DIR ~/sma); \
	port=$$($$cfg SSH_PORT 22); \
	key=$$($$cfg SSH_KEY ""); \
	branch=$$($$cfg DEPLOY_BRANCH ""); \
	remote_make=$$($$cfg REMOTE_MAKE install); \
	rsync_extra=$$($$cfg RSYNC_EXTRA ""); \
	ffmpeg_dir=$$($$cfg FFMPEG_DIR ""); \
	ssh_opts="-p $$port -o BatchMode=yes -o StrictHostKeyChecking=accept-new"; \
	[ -n "$$key" ] && ssh_opts="$$ssh_opts -i $$key"; \
	echo "==> [$$host] syncing to $$dir"; \
	rsync -az --delete \
		-e "ssh $$ssh_opts" \
		--exclude='.git/' \
		--exclude='venv/' \
		--exclude='config/' \
		--exclude='logs/' \
		--exclude='__pycache__/' \
		--exclude='*.pyc' \
		--exclude='.local' \
		--exclude='*.egg-info/' \
		$$rsync_extra \
		. $$host:$$dir; \
	if [ -n "$$branch" ]; then \
		echo "==> [$$host] checking out $$branch"; \
		ssh $$ssh_opts $$host "cd $$dir && git checkout $$branch"; \
	fi; \
	make_env=""; \
	[ -n "$$ffmpeg_dir" ] && make_env="SMA_DAEMON_FFMPEG_DIR=$$ffmpeg_dir"; \
	echo "==> [$$host] make $$remote_make$$([ -n \"$$ffmpeg_dir\" ] && echo \" (FFMPEG_DIR=$$ffmpeg_dir)\" || true)"; \
	ssh $$ssh_opts $$host "cd $$dir && $$make_env make $$remote_make"

deploy: deploy-check ## Sync code to all DEPLOY_HOSTS and run REMOTE_MAKE on each
	@failed=""; \
	for host in $(DEPLOY_HOSTS); do \
		echo ""; \
		$(MAKE) --no-print-directory deploy-host _HOST=$$host || failed="$$failed $$host"; \
	done; \
	if [ -n "$$failed" ]; then \
		echo ""; \
		echo "ERROR: deployment failed for:$$failed"; \
		exit 1; \
	fi
	@echo ""
	@echo "Deployment complete: $(DEPLOY_HOSTS)"

remote-make: deploy-check ## Run on-host make on all DEPLOY_HOSTS without syncing (usage: make remote-make REMOTE_MAKE=restart)
	@failed=""; \
	for host in $(DEPLOY_HOSTS); do \
		cfg="$(_CFG) $(_LOCAL) $$host"; \
		dir=$$($$cfg DEPLOY_DIR ~/sma); \
		port=$$($$cfg SSH_PORT 22); \
		key=$$($$cfg SSH_KEY ""); \
		ffmpeg_dir=$$($$cfg FFMPEG_DIR ""); \
		remote_make="$${REMOTE_MAKE:-$$($$cfg REMOTE_MAKE install)}"; \
		ssh_opts="-p $$port -o BatchMode=yes -o StrictHostKeyChecking=accept-new"; \
		[ -n "$$key" ] && ssh_opts="$$ssh_opts -i $$key"; \
		make_env=""; \
		[ -n "$$ffmpeg_dir" ] && make_env="SMA_DAEMON_FFMPEG_DIR=$$ffmpeg_dir"; \
		echo "==> [$$host] make $$remote_make"; \
		ssh $$ssh_opts $$host "cd $$dir && $$make_env make $$remote_make" || failed="$$failed $$host"; \
	done; \
	if [ -n "$$failed" ]; then \
		echo "ERROR: remote-make failed for:$$failed"; \
		exit 1; \
	fi
