# Fallout (2024) S01 Repair Notes

Investigation date: 2026-10-27

## Scope

Primary file:

```text
/mnt/unionfs/Media/TV/4K/Fallout (2024) {tvdb-416744}/Season 01/
  Fallout (2024) - S01E01 - The End [WEBDL-2160p][EAC3 Atmos 5.1][HDR10][x265]-NTb.mp4
```

S01E06 (`The Trap`) shows the identical defect pattern. S01E02–E05, E07 and E08 are healthy.

## Summary

| #   | Issue                                          | Affects  | Cause                                | Fix                         |
| --- | ---------------------------------------------- | -------- | ------------------------------------ | --------------------------- |
| 1   | Picture vertically stretched 2160 → 2176       | E01, E06 | Pre-3.2.4 QSV mod-32 alignment scale | Re-encode from source       |
| 2   | Cover art demoted to a real `mp4v` video track | E01, E06 | Hybrid-aspect repair remux           | Re-encode or re-tag         |
| 3   | Letterbox bars baked into the frame            | E01, E06 | Source release (not SMA)             | Optional: crop on re-encode |
| 4   | Subtitle track flagged default                 | All      | Config / disposition choice          | Verify intent               |
| 5   | Duplicate director "Matt Lake" in `iTunMOVI`   | E01      | Metadata source                      | Cosmetic                    |
| 6   | Daemon queues `Plex Versions/` files           | Library  | Path routing / scan scope            | Exclude the directory       |

## Findings

### 1. Encoder-alignment stretch (primary defect)

Probe of the main video stream:

```text
hevc Main 10  3840x2176  SAR 136:135  DAR 16:9  encoder=Lavc62.28.102 hevc_qsv
```

A true 2160p frame is 3840x2160. Releases before 3.2.4 forced mod-32 output dimensions for 10-bit QSV
through `vpp_qsv=w=...:h=...`. That filter **scales** rather than pads, so 2160 rows were resampled to 2176 —
the picture content is stretched vertically by about 0.74%. The same behaviour is visible in older logs
(job 22501: `vpp_qsv=w=1440:h=1088` for a 1080-line source).

Fixed upstream in 3.2.4 (2026-10-02): `ffmpeg: never stretch QSV output to encoder-alignment boundaries`
(commit `479d487`). See the docstring above the `vpp_qsv` builder in `resources/mediaprocessor.py`.

On 2026-10-27 20:53 the library audit's hybrid-aspect repair (`resources/library_audit/repair.py`) remuxed the
file with `-aspect 16:9`. That writes SAR 136:135 so SAR-aware players display a correct 16:9 picture, but:

- the 16 resampled rows remain in the bitstream; a remux cannot undo a scale
- players that ignore SAR for HEVC-in-MP4 (some TVs and Plex clients) still show a slightly tall image
- non-square pixels raise the chance of a Plex transcode
- the extra resampling pass costs a small amount of sharpness

Per-episode state:

| Episode | Height | SAR     | mtime            |
| ------- | ------ | ------- | ---------------- |
| E01     | 2176   | 136:135 | 2026-10-27 20:53 |
| E02     | 1600   | 1:1     | 2026-09-12 01:45 |
| E03     | 1600   | 1:1     | 2026-09-12 01:45 |
| E04     | 1600   | 1:1     | 2026-09-12 01:45 |
| E05     | 1600   | 1:1     | 2026-09-12 01:46 |
| E06     | 2176   | 136:135 | 2026-10-02 20:53 |
| E07     | 1600   | 1:1     | 2026-09-12 01:47 |
| E08     | 1600   | 1:1     | 2026-09-12 01:48 |

### 2. Repair remux broke the cover art

The repair runs:

```text
ffmpeg -i <file>.aspectfix -map 0 -c copy -aspect <dar> <file>
```

Stream layout before (E02, untouched) and after (E01/E06, repaired):

```text
E02      #3 mjpeg  tag=[0][0][0][0]  attached_pic=1
E01/E06  #3 mjpeg  tag=mp4v          attached_pic=0  SAR 8:3  handler=VideoHandler
         #4 bin_data (text)          handler=SubtitleHandler  (QuickTime chapter track)
```

Two problems in the repair command:

- `-aspect` has no stream specifier, so it is applied to **every** video output stream, including the poster
  (which now carries SAR 8:3)
- the poster loses its `attached_pic` disposition and is muxed as a second video track; some players and Plex
  may list it as an extra video stream

The added chapter text track is ffmpeg's default MP4 chapter muxing and is harmless, but differs from the
unrepaired siblings.

### 3. Baked-in letterbox

`cropdetect` across the full runtime of E01 returns a constant `crop=3840:1632:0:262` (about 2.35:1). The other
episodes are already 3840x1600 with no bars. No crop setting exists in `sma-ng.yml`, so the difference comes
from the source releases, not SMA. The bars waste bitrate but are otherwise harmless.

### 4–6. Minor

- `mov_text` subtitle track "Full" carries `default=1` on every episode; some players auto-enable it.
- `iTunMOVI` lists "Matt Lake" twice under directors.
- Jobs 26828/26829 (2026-10-02) were queued for files under `Plex Versions/Custom_ Universal TV 46289/`. They
  exited as "not in a valid format", so no damage, but they are queue noise.

## Remediation

### Media

1. Trigger a fresh download of S01E01 and S01E06 in Sonarr (4K instance), or restore the originals from
   `/recycle/` if still present.
2. Let SMA 3.2.4 or later re-encode them. Confirm the output:

   ```bash
   ffprobe -v error -select_streams v -show_entries \
     stream=index,codec_name,width,height,sample_aspect_ratio:stream_disposition=attached_pic \
     -of compact "<output>.mp4"
   ```

   Expected: main video `height=2160` (or 1600 if cropped) with `sample_aspect_ratio=1:1`, and the mjpeg stream
   with `attached_pic=1`.

3. If a re-download is not possible, at minimum restore the cover art by re-tagging the file so the poster is
   written back as `covr` (the stretch remains):

   ```bash
   python manual.py -i "<file>" -to
   ```

### Code (`resources/library_audit/repair.py`)

- Scope the aspect fix to the primary video stream: `-aspect:v:0 <dar>` instead of `-aspect <dar>`.
- Preserve cover art: either keep `-disposition:v:1 attached_pic` when the source has an attached picture, or map
  only `0:V` (non-attached video) plus audio/subtitles and re-attach the poster through the normal tag step.
- Add a regression test in `tests/test_library_audit_repair.py` asserting the generated opts never carry an
  unqualified `-aspect` and that attached pictures keep their disposition.
- Document that hybrid-aspect repair is display-only and does not remove the resampled rows, so operators
  prefer re-encode for affected files (`docs/troubleshooting.md`).

### Daemon

- Exclude `Plex Versions/` (and similar Plex optimized-version directories) from scan and routing so they are
  never enqueued.

## Verification Commands

```bash
F='/mnt/unionfs/Media/TV/4K/Fallout (2024) {tvdb-416744}/Season 01/Fallout (2024) - S01E01 - The End [WEBDL-2160p][EAC3 Atmos 5.1][HDR10][x265]-NTb.mp4'

# Dimensions, SAR, encoder
ffprobe -v error -select_streams v:0 -show_entries \
  stream=width,height,sample_aspect_ratio:stream_tags=encoder -of compact "$F"

# Stream layout and dispositions
ffprobe -v error -show_entries \
  stream=index,codec_name,codec_tag_string:stream_disposition=default,attached_pic -of compact "$F"

# Letterbox detection at a sample point
ffmpeg -hide_banner -ss 900 -i "$F" -map 0:v:0 -frames:v 24 \
  -vf cropdetect=limit=0.1:round=2:reset=0 -f null - 2>&1 | grep -o 'crop=[0-9:]*' | tail -1
```
