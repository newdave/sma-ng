.PHONY: help install install-dev install-all lint test clean daemon convert codecs docs detect-gpu config systemd-install

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
