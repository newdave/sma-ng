# Makefile — thin shim for local developer commands and on-host targets.
#
# Local development tasks (install, test, deploy, etc.) are managed by mise.
# Run `mise tasks` to list them, or `mise run <task>` to execute.
#
# On-host targets (install, config, systemd-install, restart) remain here
# because remote hosts are reached via SSH and may not have mise installed.

.PHONY: help install install-dev install-all clean config systemd-install restart install-mise \
        lint lint-fix test test-cov detect-gpu daemon convert codecs preview \
        deploy-check deploy-setup deploy remote-make \
        docker-build docker-run docker-shell docker-smoke

PYTHON ?= python3
VENV   ?= venv
PIP     = $(VENV)/bin/pip
PY      = $(VENV)/bin/python

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# On-host targets (called remotely via SSH — mise not required)
# ---------------------------------------------------------------------------

venv:
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

restart: ## Restart the sma-daemon systemd service
	sudo systemctl restart sma-daemon

systemd-install: ## Install and enable the sma-daemon systemd service
	@id sma >/dev/null 2>&1 || sudo useradd -r -s /sbin/nologin -d /opt/sma -M sma
	sudo mkdir -p /opt/sma/config /opt/sma/logs
	sudo chown -R sma:sma /opt/sma/config /opt/sma/logs
	@test -f /opt/sma/config/daemon.env || sudo install -o sma -g sma -m 640 setup/daemon.env.sample /opt/sma/config/daemon.env
	sudo chmod 755 setup/sma-daemon-start.sh
	sudo cp setup/sma-daemon.service /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable --now sma-daemon

_GPU := $(shell \
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
GPU ?= $(_GPU)

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

# ---------------------------------------------------------------------------
# mise migration helper
# ---------------------------------------------------------------------------

install-mise: ## Install mise and trust this project's mise.toml
	@if command -v mise >/dev/null 2>&1; then \
		echo "mise is already installed: $$(mise --version)"; \
	else \
		echo "Installing mise..."; \
		curl https://mise.run | sh; \
		echo ""; \
		echo "Add mise to your shell profile, e.g.:"; \
		echo "  echo 'eval \"\$$(~/.local/bin/mise activate bash)\"' >> ~/.bashrc"; \
		echo "  echo 'eval \"\$$(~/.local/bin/mise activate zsh)\"'  >> ~/.zshrc"; \
	fi
	@mise trust mise.toml 2>/dev/null || true
	@echo ""
	@echo "Run 'mise tasks' to list available tasks."
	@echo "Run 'mise run install' to set up the Python environment."

# ---------------------------------------------------------------------------
# Local shims — delegate to mise when available, fall back to direct invocation
# ---------------------------------------------------------------------------

_MISE := $(shell command -v mise 2>/dev/null)

define MISE_OR_DIRECT
  $(if $(_MISE), mise run $(1), $(2))
endef

lint: ## Run linter (ruff)
	$(call MISE_OR_DIRECT,lint,$(PY) -m ruff check .)

lint-fix: ## Run linter with auto-fix
	$(call MISE_OR_DIRECT,lint-fix,$(PY) -m ruff check --fix .)

test: ## Run tests
	$(call MISE_OR_DIRECT,test,$(PY) -m pytest tests/)

test-cov: ## Run tests with coverage
	$(call MISE_OR_DIRECT,test-cov,$(PY) -m pytest tests/ --cov=resources --cov=converter --cov-report=html)

clean: ## Remove build artifacts and caches
	$(call MISE_OR_DIRECT,clean, \
	  find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; \
	  find . -type f -name '*.pyc' -delete 2>/dev/null || true; \
	  rm -rf build/ dist/ *.egg-info/ .pytest_cache/ htmlcov/ .coverage .ruff_cache/)

detect-gpu: ## Detect GPU type for hardware acceleration
	$(call MISE_OR_DIRECT,detect-gpu,echo $(GPU))

daemon: ## Start the daemon server
	$(call MISE_OR_DIRECT,daemon,$(PY) daemon.py --host 0.0.0.0 --port 8585)

convert: ## Convert a file (usage: make convert FILE=/path/to/file.mkv)
	$(call MISE_OR_DIRECT,convert -- "$(FILE)",$(PY) manual.py -i "$(FILE)" -a)

codecs: ## List supported codecs
	$(call MISE_OR_DIRECT,codecs,$(PY) manual.py -cl)

preview: ## Preview conversion options (usage: make preview FILE=/path/to/file.mkv)
	$(call MISE_OR_DIRECT,preview -- "$(FILE)",$(PY) manual.py -i "$(FILE)" -oo)

deploy-check: ## Verify .local exists and DEPLOY_HOSTS is set
	$(call MISE_OR_DIRECT,deploy:check,$(error mise is required for deployment tasks))

deploy-setup: ## Prepare remote hosts: SSH key, ssh-copy-id, DEPLOY_DIR, ffmpeg check
	$(call MISE_OR_DIRECT,deploy:setup,$(error mise is required for deployment tasks))

deploy: ## Sync code to all DEPLOY_HOSTS and run REMOTE_MAKE on each
	$(call MISE_OR_DIRECT,deploy:run,$(error mise is required for deployment tasks))

remote-make: ## Run make target on all DEPLOY_HOSTS without syncing
	$(call MISE_OR_DIRECT,deploy:remote-make,$(error mise is required for deployment tasks))

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

TAG    ?= sma-ng:local
FFMPEG_VERSION ?= 8.0

docker-build: ## Build the Docker image locally (TAG=sma-ng:local FFMPEG_VERSION=8.0 to override)
	$(call MISE_OR_DIRECT,docker:build, \
	  docker build --target runtime --build-arg FFMPEG_VERSION=$(FFMPEG_VERSION) --tag $(TAG) .)

docker-run: ## Run the locally-built image (TAG=sma-ng:local to override)
	$(call MISE_OR_DIRECT,docker:run, \
	  mkdir -p config logs && docker run --rm -p 8585:8585 \
	    -v $(CURDIR)/config:/config -v $(CURDIR)/logs:/logs $(TAG))

docker-shell: ## Open a shell in the locally-built image
	$(call MISE_OR_DIRECT,docker:shell, \
	  docker run --rm -it -v $(CURDIR)/config:/config -v $(CURDIR)/logs:/logs \
	    --entrypoint /bin/sh $(TAG))

docker-smoke: ## Smoke-test the locally-built image (imports + ffmpeg)
	$(call MISE_OR_DIRECT,docker:smoke, \
	  docker run --rm --entrypoint python $(TAG) \
	    -c "import daemon, resources.readsettings, converter; print('imports OK')" && \
	  docker run --rm --entrypoint ffmpeg $(TAG) -version | head -2)
