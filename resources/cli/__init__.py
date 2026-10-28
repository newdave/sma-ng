"""In-image client for the `sma` host command (see setup/sma-host.sh).

Run as ``python -m resources.cli`` by the host bootstrap; it never talks to
Docker itself and returns a plan for the bootstrap to execute.
"""

from resources.cli.commands import main

__all__ = ["main"]
