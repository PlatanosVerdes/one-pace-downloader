# Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--resolution` | `1080p` | Preferred resolution (`1080p`, `720p`, `480p`). Falls back to next best if unavailable. |
| `--audio` | `subs` | `subs` = original audio with subtitles, `dub` = a dubbed track. The two never cross over: see [Which version is taken](versions.md#which-version-is-taken) |
| `--no-extended` | *(off)* | Skip Extended Cut even when available (default: prefer it) |
| `--no-dub-version` | *(off)* | Skip the Spanish dub kept beside a subtitled season as a second Plex version: see [The Spanish dub as a second version](versions.md#the-spanish-dub-as-a-second-version) |
| `--no-audio-language` | *(off)* | Leave the audio language of the files alone (default: write the language the filename implies): see [Which version is which](versions.md#which-version-is-which) |
| `--output` | `/mnt/data/series` | Root media directory |
| `--dry-run` | *(off)* | Print what would be downloaded without downloading anything |
| `--arc <id>` | *(all)* | Download a specific arc + the next one (e.g. `--arc skypiea`) |
| `--list-arcs` | *(off)* | List all available arcs and exit |
| `--pushgateway <url>` | *(off)* | Push download metrics to a Prometheus Pushgateway |
| `--no-metadata` | *(off)* | Skip fetching NFOs, posters, and season metadata from GitHub |
| `--plex-url <url>` | *(off)* | Plex server URL for automatic metadata sync (e.g. `http://localhost:32400`) |
| `--plex-token <token>` | *(off)* | Plex API token (required with `--plex-url`) |
| `--plex-path <path>` | same as `--output` | Media root path as seen by Plex. Set this if Plex runs in a container with a different mount path (e.g. `/data/series` when `--output` is `/mnt/data/series`) |

## List available arcs

```bash
python3 download.py --list-arcs
```

## Download a single arc

```bash
python3 download.py --arc skypiea --dry-run   # preview
python3 download.py --arc skypiea             # download
```
