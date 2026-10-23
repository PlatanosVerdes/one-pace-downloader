<p align="center">
  <img src="assets/icon.svg" width="96" alt="">
</p>

<h1 align="center">one-pace-downloader</h1>

<p align="center">
  <a href="https://github.com/PlatanosVerdes/one-pace-downloader/tags"><img src="https://img.shields.io/github/v/tag/PlatanosVerdes/one-pace-downloader?label=release&color=2f7d3a" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/python-3-3776ab?logo=python&logoColor=white" alt="Python">
</p>

Downloads [One Pace](https://onepace.net/es/watch) episodes, organized for Jellyfin (or any media
server that follows the `Show/Season XX/` structure). It scrapes onepace.net for the current episode
list, then downloads from the pixeldrain folders linked there, one season folder per arc.

Inspired by [one-pace-for-plex](https://github.com/SpykerNZ/one-pace-for-plex) by [@SpykerNZ](https://github.com/SpykerNZ).

> One Pace content belongs to its creators. This tool only automates downloading files that are already publicly linked on [onepace.net](https://onepace.net).

## Usage

```bash
docker build -t one-pace-downloader .
docker run --rm -v /your/media/series:/mnt/data/series one-pace-downloader \
  --resolution 1080p \
  --output /mnt/data/series
```

Without Docker: `pip install requests beautifulsoup4`, then
`python3 download.py --resolution 1080p --output /your/media/series`.

Original audio with Spanish subtitles is taken first, then English subtitles; a dub is never a
fallback. With `--plex-url` and `--plex-token` the season and episode metadata is synced to Plex
after each arc.

Add `--dry-run` to preview, `--list-arcs` to see the arcs and `--arc <id>` to fetch one.

## Docs

| | |
| :--- | :--- |
| [How it works](docs/how-it-works.md) | Spanish first, English as the fallback, and the `.lang` marker |
| [Which version is taken](docs/versions.md) | Subtitles over dubs, the Spanish dub as a second Plex version, audio language |
| [Options](docs/options.md) | Every flag, with examples |
| [Folder structure](docs/folder-structure.md) | Where episodes are saved |
| [Plex integration](docs/plex.md) | Metadata sync after each arc |
| [Metrics](docs/metrics.md) | Pushgateway metrics and coverage of the manga |

## Credits

- [One Pace](https://onepace.net) — the fan edit project
- [one-pace-for-plex](https://github.com/SpykerNZ/one-pace-for-plex) by SpykerNZ — original inspiration
