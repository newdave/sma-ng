# Custom Post-Process Scripts

SMA-NG runs scripts in this directory automatically after each conversion completes.
Set `post-process = True` in `autoProcess.ini` to enable. Scripts in the `resources/`
subdirectory are excluded from auto-execution.

The following environment variables are available to custom scripts:

| Variable | Description |
| --- | --- |
| `SMA_FILES` | JSON array of output files. First entry is the primary file; additional entries are copies created by `copy-to` |
| `SMA_TMDBID` | TMDB ID of the processed file |
| `SMA_SEASON` | Season number (TV only) |
| `SMA_EPISODE` | Episode number (TV only) |
