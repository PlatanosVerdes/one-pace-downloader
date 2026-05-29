# one-pace-downloader

A Python downloader for [One Pace](https://onepace.net/es/watch) episodes, organized for Jellyfin (or any media server that follows the `Show/Season XX/` structure).

Scrapes onepace.net to get the current episode list, then downloads from the pixeldrain folders linked there. Each arc becomes a season folder, so Jellyfin picks it up automatically.

Inspired by [one-pace-for-plex](https://github.com/SpykerNZ/one-pace-for-plex) by [@SpykerNZ](https://github.com/SpykerNZ).

> One Pace content belongs to its creators. This tool only automates downloading files that are already publicly linked on [onepace.net](https://onepace.net).

---

## How it works

1. Scrapes `onepace.net/es/watch` (Spanish) first.
2. For any arc not yet available in Spanish, falls back to `onepace.net/en/watch` automatically. Affected arcs are logged and tagged `[EN]` in the output.
3. After each download, writes a `.lang` marker per season (`es` or `en`). On subsequent runs, if a season was downloaded in English but a Spanish version is now available, the English files are deleted and re-downloaded in Spanish automatically.

---

## Usage

### Docker (recommended)

```bash
docker build -t one-pace-downloader .
docker run --rm -v /your/media/series:/mnt/data/series one-pace-downloader \
  --resolution 1080p \
  --output /mnt/data/series
```

### Python

```bash
pip install requests beautifulsoup4
python3 download.py --resolution 1080p --output /your/media/series
```

---

## Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--resolution` | `1080p` | Preferred resolution (`1080p`, `720p`, `480p`). Falls back to next best if unavailable. |
| `--audio` | `subs` | `subs` = subtitles/subtitulos, `dub` = dub/doblaje |
| `--no-extended` | *(off)* | Skip Extended Cut even when available (default: prefer it) |
| `--output` | `/mnt/data/series` | Root media directory |
| `--dry-run` | *(off)* | Print what would be downloaded without downloading anything |
| `--arc <id>` | *(all)* | Download a specific arc + the next one (e.g. `--arc skypiea`) |
| `--list-arcs` | *(off)* | List all available arcs and exit |
| `--pushgateway <url>` | *(off)* | Push download metrics to a Prometheus Pushgateway |

### List available arcs

```bash
python3 download.py --list-arcs
```

### Download a single arc

```bash
python3 download.py --arc skypiea --dry-run   # preview
python3 download.py --arc skypiea             # download
```

---

## Folder structure

Episodes are saved as:

```
<output>/
└── One Pace/
    ├── Season 01/       <- Romance Dawn
    │   ├── .lang        <- "es" or "en" (auto-upgrade marker)
    │   └── *.mp4
    ├── Season 02/       <- Orange Town
    └── ...
```

Each arc maps to a season number based on its position on onepace.net. Season numbers are stable: arcs keep their number even if earlier arcs are missing.

---

## Metrics

If you run a Prometheus Pushgateway, pass `--pushgateway http://pushgateway:9091` to get:

**Aggregate (per run):**
- `onepace_episodes_new_downloads`
- `onepace_episodes_on_disk`
- `onepace_episodes_failed`
- `onepace_arcs_done` / `onepace_arcs_total`
- `onepace_last_run_seconds`

**Per arc** (pushed to `/type/arc-status` grouping key):
- `onepace_arc_episodes_on_disk{arc_id, title, season, available_es, available_en, lang}`

The per-arc metric powers the Arc Status table in Grafana, showing which arcs are available in Spanish/English and what language is currently on disk.

---

## Credits

- [One Pace](https://onepace.net) — the fan edit project
- [one-pace-for-plex](https://github.com/SpykerNZ/one-pace-for-plex) by SpykerNZ — original inspiration
