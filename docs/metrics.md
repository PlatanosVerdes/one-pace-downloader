# Metrics and coverage

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
