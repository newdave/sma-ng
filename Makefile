# Makefile — thin shim for local developer commands.
#
# Local development tasks (install, test, deploy, etc.) are managed by mise.
# Run `mise tasks` to list them, or `mise run <task>` to execute.

.PHONY: help install install-dev install-all clean config systemd-install restart install-mise venv \
        lint lint-fix test test-cov detect-gpu daemon convert codecs preview rename \
        deploy-check deploy-setup deploy remote-task remote-make \
        docker-build docker-run docker-shell docker-smoke

PYTHON ?= python3
VENV   ?= venv
PIP     = $(VENV)/bin/pip
PY      = $(VENV)/bin/python

_MISE := $(shell command -v mise 2>/dev/null)

define MISE_OR_DIRECT
  $(if $(_MISE), mise run $(1), $(2))
endef

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup and service shims
# ---------------------------------------------------------------------------

venv:
	$(call MISE_OR_DIRECT,setup:venv, \
	  $(PYTHON) -m venv $(VENV); \
	  chmod 755 $(VENV) $(VENV)/bin || true; \
	  chmod 755 $(VENV)/bin/python $(VENV)/bin/python3 $(VENV)/bin/python3.* 2>/dev/null || true; \
	  $(PIP) install --upgrade pip)

install: venv ## Install base dependencies
	$(call MISE_OR_DIRECT,setup:deps,$(PIP) install -r setup/requirements.txt)

install-dev: install ## Install dev dependencies (lint, test)
	$(call MISE_OR_DIRECT,setup:deps:dev,$(PIP) install -e ".[dev]")

install-all: install ## Install all optional dependencies
	$(call MISE_OR_DIRECT,setup:deps:all, \
	  $(PIP) install -r setup/requirements-qbittorrent.txt; \
	  $(PIP) install -r setup/requirements-deluge.txt; \
	  $(PIP) install -e ".[dev]")

restart: ## Restart the sma-daemon systemd service
	$(call MISE_OR_DIRECT,systemd:restart,sudo systemctl restart sma-daemon)

SERVICE_USER ?= $(shell whoami)

systemd-install: ## Install and enable the sma-daemon systemd service (SERVICE_USER=<user> to override)
	$(call MISE_OR_DIRECT,systemd:install, \
	  sudo mkdir -p /opt/sma/config /opt/sma/logs; \
	  sudo chown -R $(SERVICE_USER): /opt/sma/config /opt/sma/logs; \
	  test -f /opt/sma/config/daemon.env || sudo install -o $(SERVICE_USER) -m 640 setup/daemon.env.sample /opt/sma/config/daemon.env; \
	  sudo chmod 755 setup/sma-daemon-start.sh; \
	  sed 's/^User=.*/User=$(SERVICE_USER)/; s/^Group=.*/Group=$(SERVICE_USER)/' setup/sma-daemon.service \
	    | sudo tee /etc/systemd/system/sma-daemon.service > /dev/null; \
	  sudo systemctl daemon-reload; \
	  sudo systemctl enable --now sma-daemon)

GPU ?= $(shell ./scripts/detect-gpu.sh)

config: ## Create config with GPU auto-detection (GPU=<type> to override)
	GPU="$(GPU)" $(call MISE_OR_DIRECT,config:generate,./scripts/generate-configs.sh)

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
	@echo "Run 'mise run setup:deps' to set up the Python environment."

# ---------------------------------------------------------------------------
# Local shims — delegate to mise when available, fall back to direct invocation
# ---------------------------------------------------------------------------

lint: ## Run linter (ruff)
	$(call MISE_OR_DIRECT,test:lint,$(PY) -m ruff check .)

lint-fix: ## Run linter with auto-fix
	$(call MISE_OR_DIRECT,dev:lint,$(PY) -m ruff check --fix .)

test: ## Run tests
	$(call MISE_OR_DIRECT,test,$(PY) -m pytest)

test-cov: ## Run tests with coverage
	$(call MISE_OR_DIRECT,test:cov,$(PY) -m pytest --cov=resources --cov=converter --cov=autoprocess --cov-report=html --cov-report=term-missing:skip-covered)

clean: ## Remove build artifacts and caches
	$(call MISE_OR_DIRECT,setup:clean, \
	  find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; \
	  find . -type f -name '*.pyc' -delete 2>/dev/null || true; \
	  rm -rf build/ dist/ *.egg-info/ .pytest_cache/ htmlcov/ .coverage .ruff_cache/)

detect-gpu: ## Detect GPU type for hardware acceleration
	$(call MISE_OR_DIRECT,config:detect:gpu,./scripts/detect-gpu.sh)

daemon: ## Start the daemon server
	$(call MISE_OR_DIRECT,daemon:start,$(PY) daemon.py --host 0.0.0.0 --port 8585)

convert: ## Convert a file (usage: make convert FILE=/path/to/file.mkv)
	$(call MISE_OR_DIRECT,media:convert -- "$(FILE)",$(PY) manual.py -i "$(FILE)" -a)

codecs: ## List supported codecs
	$(call MISE_OR_DIRECT,media:codecs,$(PY) manual.py -cl)

preview: ## Preview conversion options (usage: make preview FILE=/path/to/file.mkv)
	$(call MISE_OR_DIRECT,media:preview -- "$(FILE)",$(PY) manual.py -i "$(FILE)" -oo)

rename: ## Rename media files using naming templates (usage: make rename FILE=/path/to/file-or-dir)
	$(call MISE_OR_DIRECT,media:rename -- "$(FILE)",$(PY) rename.py "$(FILE)")

deploy-check: ## Verify .local exists and DEPLOY_HOSTS is set
	$(call MISE_OR_DIRECT,deploy:check,echo "mise is required for deployment tasks" >&2; exit 1)

deploy-setup: ## Prepare remote hosts: SSH key, ssh-copy-id, DEPLOY_DIR, ffmpeg check
	$(call MISE_OR_DIRECT,deploy:setup,echo "mise is required for deployment tasks" >&2; exit 1)

deploy: ## Sync code to all DEPLOY_HOSTS and install base dependencies
	$(call MISE_OR_DIRECT,deploy:run,echo "mise is required for deployment tasks" >&2; exit 1)

remote-task: ## Run a mise task on all DEPLOY_HOSTS without syncing
	$(call MISE_OR_DIRECT,deploy:remote:task,echo "mise is required for deployment tasks" >&2; exit 1)

remote-make: ## Legacy alias: set REMOTE_TASK from REMOTE_MAKE and run remote-task
	REMOTE_TASK="$${REMOTE_MAKE:-$${REMOTE_TASK:-setup:deps}}" $(call MISE_OR_DIRECT,deploy:remote:task,echo "mise is required for deployment tasks" >&2; exit 1)

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

TAG    ?= sma-ng:local
FFMPEG_VERSION ?= 8.1

docker-build: ## Build the Docker image locally (TAG=sma-ng:local FFMPEG_VERSION=8.0 to override)
	$(call MISE_OR_DIRECT,docker:build, \
	  docker build --file docker/Dockerfile --target runtime --build-arg FFMPEG_VERSION=$(FFMPEG_VERSION) --tag $(TAG) .)

docker-run: ## Run the locally-built image (TAG=sma-ng:local to override)
	TAG="$(TAG)" $(call MISE_OR_DIRECT,docker:run,./scripts/docker-run.sh)

docker-shell: ## Open a shell in the locally-built image
	$(call MISE_OR_DIRECT,docker:shell, \
	  docker run --rm -it -v $(CURDIR)/config:/config -v $(CURDIR)/logs:/logs \
	    --entrypoint /bin/sh $(TAG))

docker-smoke: ## Smoke-test the locally-built image (imports + ffmpeg)
	$(call MISE_OR_DIRECT,docker:smoke, \
	  docker run --rm --entrypoint python3 $(TAG) \
	    scripts/docker_smoke_imports.py && \
	  docker run --rm --entrypoint ffmpeg $(TAG) -version | head -2)
