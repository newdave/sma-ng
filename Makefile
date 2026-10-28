# Makefile — bootstrap only. All tasks live in mise (`mise tasks` lists them).
#
#   make install-mise   install mise and trust this project's mise.toml
#   make                show this note and the mise equivalents of the old targets

.PHONY: help install-mise
.DEFAULT_GOAL := help

help:
	@echo "SMA-NG tasks run through mise. Install it with 'make install-mise', then:"
	@echo ""
	@echo "  mise run setup:deps:dev        # create venv + install dev dependencies (old: make install-dev)"
	@echo "  mise run config:generate       # config/sma-ng.yml with GPU auto-detected (old: make config)"
	@echo "  mise run config:gpu            # show the detected GPU (old: make detect-gpu)"
	@echo "  mise run test / test:cov       # tests / coverage gates (old: make test, make test-cov)"
	@echo "  mise run test:lint / dev:lint  # lint / lint with auto-fix (old: make lint, make lint-fix)"
	@echo "  mise run daemon:start          # start the daemon (old: make daemon)"
	@echo "  mise run media:convert -- FILE # convert a file (old: make convert FILE=...)"
	@echo "  mise run deploy:remote         # build config and redeploy Docker hosts (old: make deploy)"
	@echo ""
	@echo "Run 'mise tasks' for the full list."

install-mise:
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
	@echo "Run 'mise run setup:deps:dev' to set up the Python environment, then 'mise tasks' to list tasks."

# Old targets point at their mise equivalent instead of failing silently.
# They are declared explicitly because some share a name with a directory
# (e.g. config/), which would otherwise make them "up to date".
OLD_TARGETS := venv install install-dev install-all config lint lint-fix test test-cov clean detect-gpu \
               daemon convert codecs preview rename deploy-check deploy-setup deploy remote-task remote-make \
               docker-build docker-run docker-shell docker-smoke
.PHONY: $(OLD_TARGETS)
$(OLD_TARGETS):
	@echo "make $@ was removed; tasks run through mise. Run 'make' for the equivalents or 'mise tasks' for the full list." >&2
	@exit 2

.DEFAULT:
	@echo "make $@: no such target. Tasks run through mise; run 'mise tasks' for the full list." >&2
	@exit 2
