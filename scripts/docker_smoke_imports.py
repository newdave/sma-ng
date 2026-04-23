"""Smoke-test imports expected to work inside the runtime Docker image."""

import converter  # noqa: F401
import daemon  # noqa: F401
import resources.readsettings  # noqa: F401

print("imports OK")
