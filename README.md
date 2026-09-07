# one-pace-downloader

A Python downloader for [One Pace](https://onepace.net/es/watch) episodes, organized for Jellyfin (or any media server that follows the `Show/Season XX/` structure).

Scrapes onepace.net to get the current episode list, then downloads from the pixeldrain folders linked there. Each arc becomes a season folder, so Jellyfin picks it up automatically.

Inspired by [one-pace-for-plex](https://github.com/SpykerNZ/one-pace-for-plex) by [@SpykerNZ](https://github.com/SpykerNZ).

> One Pace content belongs to its creators. This tool only automates downloading files that are already publicly linked on [onepace.net](https://onepace.net).

---

## How it works

1. Scrapes `onepace.net/es/watch` (Spanish) first.
2. For any arc not yet available in Spanish, falls back to `onepace.net/en/watch` automatically. Affected arcs are logged and tagged `[EN]` in the output.
3. After each download, writes a `.lang` marker per season (`es-subs`, `en-subs`, `es-dub`). On subsequent runs a season is deleted and re-downloaded when either its marker or its actual files stop matching what the preferences ask for. The marker catches an arc reaching the Spanish page, or one that has to stop using what it was taking before. The files catch what the marker cannot: a run that replaced half a season and died, or one that wrote the marker before an older file was removed. Files whose variant cannot be read from the name are left alone rather than replaced on every pass forever, and so are the dub files kept as a second version, which say nothing about whether the season itself is right. A season holding nothing but dubbed files still does.

---

## Which version is taken

In order, and the first one that exists wins:

1. Original audio with Spanish subtitles (`Subtitulos en español`).
2. Original audio with English subtitles (`English Subtitles`).

A dubbed track is never taken as a fallback. Some arcs are offered on the Spanish page only as `Doblaje en español`, and treating that as "the Spanish version" is how a Castilian-audio Water Seven ended up in the library: an arc like that skips the Spanish page entirely and takes English subtitles over the same original audio.

`--audio dub` reverses the preference, and is just as strict: it takes a dub or nothing.

### The Spanish dub as a second version

A dub is never a fallback, but it is worth having beside the subtitled files rather than instead of them. Plex reads two files of the same episode as two versions of it and offers both under *Play Version*, so a season plays either with Spanish audio or with the original audio and subtitles.

It is downloaded for the episodes that have one, which is usually fewer than the arc holds: One Pace dubs an arc long after it subtitles it, so Water Seven is 20 subtitled episodes and 5 dubbed ones.

What should play is the original audio with Spanish subtitles, and Plex orders the versions of an episode by resolution and plays the first. So in a season taken with Spanish subtitles the dub is held to that resolution or lower, never higher: at equal resolution Plex keeps the file that was already there, which is the subtitled one, and an arc publishing its dub only above it is skipped. A season taken with English subtitles has nothing to protect, so there the dub is taken at `--resolution` like anything else and may well be what a click on play gives you.

Neither version costs the server any transcoding: both are h264 with AAC stereo, and the subtitles are burnt into the subtitled release, so there is nothing to convert or to burn in on the fly. It is a version change and not an audio-track change, because the two files are separate encodes, and the dub carries no subtitles at all.

`--no-dub-version` turns this off. Dub files already on disk are left where they are.

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
| `--audio` | `subs` | `subs` = original audio with subtitles, `dub` = a dubbed track. The two never cross over: see [Which version is taken](#which-version-is-taken) |
| `--no-extended` | *(off)* | Skip Extended Cut even when available (default: prefer it) |
| `--no-dub-version` | *(off)* | Skip the Spanish dub kept beside a subtitled season as a second Plex version: see [The Spanish dub as a second version](#the-spanish-dub-as-a-second-version) |
| `--output` | `/mnt/data/series` | Root media directory |
| `--dry-run` | *(off)* | Print what would be downloaded without downloading anything |
| `--arc <id>` | *(all)* | Download a specific arc + the next one (e.g. `--arc skypiea`) |
| `--list-arcs` | *(off)* | List all available arcs and exit |
| `--pushgateway <url>` | *(off)* | Push download metrics to a Prometheus Pushgateway |
| `--no-metadata` | *(off)* | Skip fetching NFOs, posters, and season metadata from GitHub |
| `--plex-url <url>` | *(off)* | Plex server URL for automatic metadata sync (e.g. `http://localhost:32400`) |
| `--plex-token <token>` | *(off)* | Plex API token (required with `--plex-url`) |
| `--plex-path <path>` | same as `--output` | Media root path as seen by Plex. Set this if Plex runs in a container with a different mount path (e.g. `/data/series` when `--output` is `/mnt/data/series`) |

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
    │   ├── .lang        <- "es-subs", "en-subs" or "es-dub" (replacement marker)
    │   ├── *[Es Dub]*   <- second version, only for the episodes that have a dub
    │   └── *.mp4
    ├── Season 02/       <- Orange Town
    └── ...
```

Each arc maps to a season number based on its position on onepace.net. Season numbers are stable: arcs keep their number even if earlier arcs are missing.

---

## Plex integration

Pass `--plex-url` and `--plex-token` to automatically sync metadata to Plex after each arc is processed. Whenever new episodes are downloaded or new NFOs are written, the downloader will:

1. Inject the season title and description (from `season.nfo`) into Plex.
2. Upload the season poster (from `poster.png`) to Plex.
3. Inject episode titles and descriptions (from per-episode NFOs) for any episodes not already matched to Plex's cloud database.

```bash
docker run --rm -v /your/media/series:/mnt/data/series one-pace-downloader \
  --resolution 1080p \
  --output /mnt/data/series \
  --plex-url http://plex:32400 \
  --plex-token YOUR_TOKEN
```

If Plex runs in a separate container where the media is mounted at a different path, use `--plex-path` to tell the downloader how Plex sees the files:

```bash
  --plex-path /data/series   # path as Plex's container sees it
```

> The Plex library must have **Local Media Assets** enabled (it is by default). The `--plex-url` and `--plex-token` flags are both required for sync to activate; omitting either silently skips Plex integration.

A dub downloaded as a second version appears once Plex has scanned the season, which it does on its own if the library watches the folder. `PUT /library/metadata/<season key>/refresh` forces it.

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
- `onepace_arc_episodes_on_disk{arc_id, title, season, available_es, available_en, lang, audio, subtitles, canon}`
- `onepace_arc_files_on_disk{arc_id, title, season, audio, subtitles}`
- `onepace_canon_arcs_total`, `onepace_canon_arcs_covered`, `onepace_canon_arc_missing{title}`

The first is one series per season and powers the Arc Status table in Grafana. It counts and describes the season's own episodes, so a dub kept as a second version of some of them neither inflates the count nor turns the season `mixed`. A season holding more than one variant of its own episodes, which happens part-way through a replacement, reads `mixed`; an empty one reads `none`.

The second is one series per audio/subtitle pair over every file on disk, dub versions included, which is what a `mixed` season needs to be readable: it shows both halves separately.

In both, `audio` and `subtitles` come from the filename:

| filename tag | `audio` | `subtitles` |
|---|---|---|
| `[Es Sub]` | `original` | `es` |
| `[En Sub]` | `original` | `en` |
| `[Es Dub]` | `es` | `none` |
| no tag | `unknown` | `unknown` |

## Coverage of the manga

One Pace publishes what it has finished, so its own arc list cannot answer "what is still missing". That comes from the One Piece wiki's `Category:Story Arcs`, which is the manga's arcs and deliberately excludes anime filler, since One Pace adapts the manga and never touches filler.

- `onepace_canon_arcs_total` is how many story arcs the manga has.
- `onepace_canon_arcs_covered` is how many of them One Pace has released.
- `onepace_canon_arc_missing{title}` is one series per arc it has not.
- `canon` on the per-season metric marks which rows are a story arc at all. One Pace also releases cover stories and specials, which are not arcs and are why its own count is higher than the manga's.

A handful of arcs are romanised differently on each side, so `ARC_ALIASES` maps them; without it the report claims arcs are missing that are on disk. A new arc with a new spelling needs a line there.

When the wiki cannot be reached the coverage metrics are not pushed at all, so the last known values stand rather than the run claiming every arc is missing, and `canon` reports `unknown` until the next successful pass.

---

`lang` still comes off the `.lang` marker, so it records what a run meant to fetch while `audio` and `subtitles` record what it got. The two disagreeing is worth looking at. `sum(onepace_arc_files_on_disk{audio!="original"})` is the count of dubbed files on disk, whatever the markers claim; with second versions in the library it is no longer expected to be zero, so what says a season came down dubbed is `onepace_arc_episodes_on_disk{audio!="original"}`, which describes the season itself and should be empty unless the run asked for `--audio dub`.

---

## Credits

- [One Pace](https://onepace.net) — the fan edit project
- [one-pace-for-plex](https://github.com/SpykerNZ/one-pace-for-plex) by SpykerNZ — original inspiration
