# Custom Post-Process Scripts

![SMA-NG](../logo.png)

SMA-NG runs scripts in this directory automatically after each conversion completes.
Set `post-process: true` under `base.converter` in `sma-ng.yml` to enable.
Scripts in the `resources/` subdirectory are excluded from auto-execution.

Files are skipped (never run as hooks) when they:

- start with `_` (private helpers, for example `_helpers.py`)
- have an excluded extension (`.txt`, `.log`, `.pyc`, `.md`), or are listed in the excluded file names
- are directories

Put shared helper code in `_`-prefixed files or in `resources/`.

The following environment variables are available to custom scripts:

| Variable       | Description |
| -------------- | ----------- |
| `SMA_FILES`    | JSON array of output files. First entry is the primary file; additional entries are copies created by `copy-to` |
| `SMA_TMDBID`   | TMDB ID of the processed file |
| `SMA_SEASON`   | Season number (TV only) |
| `SMA_EPISODE`  | Episode number (TV only). For a multi-episode file, the first episode |
| `SMA_EPISODES` | Comma-separated list of every episode number (TV only, for example `1,2`; a single episode yields just that number) |

Note: `setup/post_process/` is unrelated. It holds sample download-client submitter scripts, not conversion hooks.
