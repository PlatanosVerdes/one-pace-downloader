# Plex integration

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
