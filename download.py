#!/usr/bin/env python3
"""
One Pace downloader for Jellyfin.

Scrapes onepace.net/es/watch (Spanish), then onepace.net/en/watch as fallback
for arcs not yet available in Spanish. Maps each arc to a Season folder and
downloads from pixeldrain (API, no auth required for public files).

Usage:
    python3 download.py [--resolution 1080p] [--output /mnt/data/series]
    python3 download.py --dry-run       # show what would be downloaded
    python3 download.py --check-new     # only download arcs with new files
"""

import argparse
import collections
import re
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

ONEPACE_URL_ES = "https://onepace.net/es/watch"
ONEPACE_URL_EN = "https://onepace.net/en/watch"
PIXELDRAIN_API = "https://pixeldrain.com/api"
GITHUB_RAW = "https://raw.githubusercontent.com/SpykerNZ/one-pace-for-plex/main"
GITHUB_API = "https://api.github.com/repos/SpykerNZ/one-pace-for-plex"
SHOW_DIR_NAME = "One Pace"
RESOLUTIONS = ["1080p", "720p", "480p"]
# A pixeldrain folder holds whatever the release group left there, notes included. One of them,
# `wip.md`, answers 451 Unavailable For Legal Reasons, so every nightly run ended with one failure
# and the "One Pace downloads failing" alert could never clear: twelve runs in a row before anyone
# read it. Only video files were ever wanted.
VIDEO_SUFFIXES = {".mp4", ".mkv", ".avi", ".m4v"}
LANG_MARKER = ".lang"
# One Pace publishes what it has finished, not what the manga contains, so the list of arcs
# that exist has to come from somewhere else. The wiki separates story arcs from filler, which
# matters: One Pace adapts the manga and never touches anime filler.
WIKI_API = "https://onepiece.fandom.com/api.php"
STORY_ARCS_CATEGORY = "Category:Story Arcs"
# The wiki and One Pace romanise a handful of arcs differently. Without these the coverage
# report claims arcs are missing that are sitting on disk.
ARC_ALIASES = {
    "arabasta": "alabasta",
    "water7": "waterseven",
    "wanocountry": "wano",
    "levely": "reverie",
}
# Every release names its variant in the filename: [Es Sub], [En Sub], [Es Dub].
VARIANT_IN_NAME = re.compile(r"\[(Es|En) (Sub|Dub)\]", re.IGNORECASE)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _arc_key(name: str) -> str:
    """A comparable key for an arc, from either a wiki title or a One Pace slug."""
    key = re.sub(r"\s+arc$", "", name.strip(), flags=re.I).lower().replace("-", " ")
    key = re.sub(r"[^a-z0-9]", "", key)
    return ARC_ALIASES.get(key, key)


def fetch_canon_arcs() -> list[str] | None:
    """Titles of every story arc the manga has, or None when the wiki cannot be reached.

    None rather than an empty list: nothing is worse than reporting that every arc is
    missing because a lookup timed out.
    """
    params = {"action": "query", "list": "categorymembers", "cmtitle": STORY_ARCS_CATEGORY,
              "cmtype": "page", "cmlimit": "500", "format": "json"}
    try:
        resp = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        members = resp.json()["query"]["categorymembers"]
    except Exception as exc:
        print(f"  [warn] canon arc list unavailable: {exc}")
        return None
    # The category also holds its own index page, which is not an arc.
    return [m["title"] for m in members if m["title"].endswith(" Arc")]


def _variant_from_name(name: str) -> tuple[str, str]:
    """(audio, subtitles) for one file, read off its name rather than off the marker: the
    marker is what the script meant to fetch, the name is what is actually on disk."""
    match = VARIANT_IN_NAME.search(name)
    if not match:
        return "unknown", "unknown"
    language, kind = match.group(1).lower(), match.group(2).lower()
    if kind == "dub":
        return language, "none"
    return "original", language


def _season_variant(counts: collections.Counter) -> tuple[str, str]:
    """One (audio, subtitles) pair for a whole season, for the per-season metric."""
    if not counts:
        return "none", "none"
    if len(counts) > 1:
        return "mixed", "mixed"
    return next(iter(counts))


def _escape_label(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def push_metrics(pgw_url: str, stats: dict) -> None:
    """Push download progress to Prometheus Pushgateway."""
    if not pgw_url:
        return
    on_disk = stats.get("downloaded", 0) + stats.get("skipped", 0)
    payload = (
        "# TYPE onepace_episodes_new_downloads gauge\n"
        f"onepace_episodes_new_downloads {stats.get('downloaded', 0)}\n"
        "# TYPE onepace_episodes_on_disk gauge\n"
        f"onepace_episodes_on_disk {on_disk}\n"
        "# TYPE onepace_episodes_failed gauge\n"
        f"onepace_episodes_failed {stats.get('failed', 0)}\n"
        "# TYPE onepace_arcs_done gauge\n"
        f"onepace_arcs_done {stats.get('arcs_done', 0)}\n"
        "# TYPE onepace_arcs_total gauge\n"
        f"onepace_arcs_total {stats.get('arcs_total', 0)}\n"
        "# TYPE onepace_last_run_seconds gauge\n"
        f"onepace_last_run_seconds {int(time.time())}\n"
    )
    try:
        requests.post(
            f"{pgw_url.rstrip('/')}/metrics/job/one-pace-downloader",
            data=payload,
            headers={"Content-Type": "text/plain"},
            timeout=5,
        )
    except Exception as exc:
        print(f"  [warn] Pushgateway unreachable: {exc}")


def push_arc_metrics(pgw_url: str, arcs: list[dict], show_dir: Path,
                     canon: list[str] | None = None) -> None:
    """Push per-arc status metrics to Pushgateway under a separate grouping key."""
    if not pgw_url:
        return
    canon_keys = {_arc_key(t): t for t in canon} if canon is not None else None
    lines = ["# TYPE onepace_arc_episodes_on_disk gauge"]
    variants: list[str] = ["# TYPE onepace_arc_files_on_disk gauge"]
    for arc in arcs:
        season_dir = show_dir / f"Season {arc['season']:02d}"
        videos = ([f for f in season_dir.iterdir() if f.suffix.lower() in VIDEO_SUFFIXES]
                  if season_dir.exists() else [])
        lang_on_disk, _ = _marker_parts(_read_lang_marker(season_dir))
        counts = collections.Counter(_variant_from_name(f.name) for f in videos)
        arc_labels = (
            f'arc_id="{_escape_label(arc["arc_id"])}",'
            f'title="{_escape_label(arc["title"])}",'
            f'season="{arc["season"]:02d}"'
        )
        audio, subtitles = _season_variant(counts)
        if canon_keys is None:
            is_canon = "unknown"
        else:
            is_canon = "true" if _arc_key(arc["arc_id"]) in canon_keys else "false"
        labels = (
            f'{arc_labels},'
            f'available_es="{arc["available_es"]}",'
            f'available_en="{arc["available_en"]}",'
            f'lang="{lang_on_disk or "none"}",'
            f'audio="{audio}",subtitles="{subtitles}",'
            f'canon="{is_canon}"'
        )
        lines.append(f"onepace_arc_episodes_on_disk{{{labels}}} {len(videos)}")

        # Also per audio/subtitle pair, which the season labels above cannot express: a
        # season part-way through a replacement holds both, and reads "mixed" up there.
        for (audio, subtitles), count in sorted(counts.items()):
            variants.append(
                f'onepace_arc_files_on_disk{{{arc_labels},'
                f'audio="{audio}",subtitles="{subtitles}"}} {count}'
            )
    if canon_keys is not None:
        covered = {_arc_key(a["arc_id"]) for a in arcs} & set(canon_keys)
        lines.append("# HELP onepace_canon_arcs_total Story arcs the manga has, per the wiki\n"
                     "# TYPE onepace_canon_arcs_total gauge\n"
                     f"onepace_canon_arcs_total {len(canon_keys)}")
        lines.append("# HELP onepace_canon_arcs_covered Story arcs One Pace has released\n"
                     "# TYPE onepace_canon_arcs_covered gauge\n"
                     f"onepace_canon_arcs_covered {len(covered)}")
        lines.append("# HELP onepace_canon_arc_missing Story arc One Pace has not released\n"
                     "# TYPE onepace_canon_arc_missing gauge")
        for key, title in sorted(canon_keys.items(), key=lambda kv: kv[1]):
            if key not in covered:
                lines.append(f'onepace_canon_arc_missing{{title="{_escape_label(title)}"}} 1')

    payload = "\n".join(lines + variants) + "\n"
    try:
        requests.post(
            f"{pgw_url.rstrip('/')}/metrics/job/one-pace-downloader/type/arc-status",
            data=payload,
            headers={"Content-Type": "text/plain"},
            timeout=5,
        )
    except Exception as exc:
        print(f"  [warn] Pushgateway arc metrics unreachable: {exc}")


def _read_lang_marker(season_dir: Path) -> str | None:
    p = season_dir / LANG_MARKER
    return p.read_text().strip() if p.exists() else None


def _write_lang_marker(season_dir: Path, marker: str) -> None:
    season_dir.mkdir(parents=True, exist_ok=True)
    (season_dir / LANG_MARKER).write_text(marker)


def _marker_parts(marker: str | None) -> tuple[str | None, str | None]:
    """Split a marker into (language, kind). An older marker holds only a language."""
    if not marker:
        return None, None
    lang, _, kind = marker.partition("-")
    return lang, kind or None


def _season_videos(season_dir: Path) -> list[Path]:
    if not season_dir.exists():
        return []
    return [f for f in season_dir.iterdir() if f.suffix.lower() in VIDEO_SUFFIXES]


def _marker_variant(marker: str) -> tuple[str, str]:
    """The (audio, subtitles) a marker asks for, in the same shape _variant_from_name reads
    off a filename, so the two can be compared."""
    language, kind = _marker_parts(marker)
    if kind == "dub":
        return language or "unknown", "none"
    return "original", language or "unknown"


def _disk_disagrees(season_dir: Path, marker: str) -> tuple[str, str] | None:
    """The (audio, subtitles) actually on disk when it is not what `marker` asks for.

    A marker can be right about the past and wrong about the present: a run that replaced
    half a season and died, or one that wrote the marker before an older file was removed.
    Files nobody can classify are left alone rather than replaced on every pass forever.
    """
    counts = collections.Counter(_variant_from_name(f.name) for f in _season_videos(season_dir))
    if not counts:
        return None
    found = _season_variant(counts)
    if found == ("unknown", "unknown") or found == _marker_variant(marker):
        return None
    return found


def _needs_replacing(stored: str | None, current: str) -> bool:
    """True when what is on disk was taken under preferences that no longer apply. Only the
    fields the stored marker carries are compared."""
    stored_lang, stored_kind = _marker_parts(stored)
    if stored_lang is None:
        return False
    lang, kind = _marker_parts(current)
    if stored_lang != lang:
        return True
    return stored_kind is not None and stored_kind != kind


def _clear_season(season_dir: Path, reason: str, dry_run: bool) -> None:
    """Delete the season's videos so the right variant can be downloaded fresh."""
    videos = [f for f in season_dir.iterdir() if f.suffix.lower() in VIDEO_SUFFIXES]
    if dry_run:
        print(f"  [dry]  would replace {len(videos)} file(s): {reason}")
        return
    for f in videos:
        f.unlink()
        print(f"  [del]  {f.name} ({reason})")
    (season_dir / LANG_MARKER).unlink(missing_ok=True)


def check_connectivity() -> None:
    """Abort early if Pi-hole or another blocker is intercepting pixeldrain DNS."""
    try:
        ip = socket.gethostbyname("pixeldrain.com")
    except OSError as exc:
        sys.exit(f"DNS lookup for pixeldrain.com failed: {exc}")
    if ip.startswith("127.") or ip in ("0.0.0.0", "::1"):
        sys.exit(
            f"ERROR: pixeldrain.com resolves to {ip} — likely blocked by Pi-hole.\n"
            "Fix: run via Docker (uses DNS override) or whitelist pixeldrain.com in Pi-hole:\n"
            "  docker exec pihole pihole --white-add pixeldrain.com"
        )

TVSHOW_NFO = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<tvshow>
  <title>One Pace</title>
  <originaltitle>One Pace</originaltitle>
  <plot>One Pace es una edición del fan sin el relleno de One Piece, editada para que el ritmo del anime siga el del manga original.</plot>
  <genre>Anime</genre>
  <genre>Action</genre>
  <genre>Adventure</genre>
</tvshow>
"""


def _parse_arc_groups(li) -> list[dict]:
    """Extract all language/variant groups from an arc <li> element."""
    groups_ul = li.find("ul")
    if not groups_ul:
        return []
    groups = []
    for group_li in groups_ul.find_all("li", recursive=False):
        label_div = group_li.find("div")
        label = label_div.get_text(strip=True) if label_div else ""
        links_ul = group_li.find("ul")
        if not links_ul:
            continue
        links: dict[str, str] = {}
        for a in links_ul.find_all("a"):
            spans = a.find_all("span")
            res_text = spans[-1].get_text(strip=True) if spans else ""
            m = re.search(r"(\d{3,4}p)", res_text)
            if m and a.get("href"):
                links[m.group(1)] = a["href"]
        if links:
            groups.append({"label": label, "links": links})
    return groups


def _is_subs(group: dict) -> bool:
    label = group["label"].lower()
    return "subtitulo" in label or "subtitle" in label


def _is_dub(group: dict) -> bool:
    label = group["label"].lower()
    return "doblaje" in label or ("dub" in label and "subtitle" not in label)


def _kind(group: dict) -> str:
    return "dub" if _is_dub(group) else "subs"


def _pick_group(groups: list[dict], audio: str, extended: bool) -> dict | None:
    """
    Choose the best group given audio preference and extended preference.
    audio: 'subs' subtitles over the original audio, 'dub' a dubbed track. Returns None
           rather than the other one, so the caller can fall back to the other page.
    extended: True  = prefer Extended Cut over regular when available
              False = prefer regular, ignore Extended Cut
    Alternate Cut (e.g. G-8) is never picked automatically.
    """
    def lower(g): return g["label"].lower()
    is_extended = lambda g: "extended cut" in lower(g)
    is_alternate = lambda g: "alternate cut" in lower(g)
    is_regular  = lambda g: not is_extended(g) and not is_alternate(g)

    wanted = _is_dub if audio == "dub" else _is_subs
    candidates = [g for g in groups if wanted(g) and not is_alternate(g)]
    if not candidates:
        return None
    if extended:
        return next(
            (g for g in candidates if is_extended(g)),
            next((g for g in candidates if is_regular(g)), candidates[0]),
        )
    return next((g for g in candidates if is_regular(g)), candidates[0])


def _pick_resolution(links: dict, resolution: str) -> tuple[str, str] | None:
    """Return (chosen_res, url) with fallback to next best resolution."""
    for res in [resolution] + [r for r in RESOLUTIONS if r != resolution]:
        if res in links:
            return res, links[res]
    return None


def _scrape_page(url: str, resolution: str, audio: str, extended: bool) -> tuple[list[str], dict[str, dict]]:
    """
    Scrape a onepace.net watch page.
    Returns (ordered_arc_ids, resolved_arcs) where resolved_arcs maps arc_id -> arc dict
    for arcs that have downloadable content matching the given preferences.
    """
    print(f"Fetching {url} ...")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    arc_lis = [
        li for li in soup.find_all("li", attrs={"id": True, "aria-labelledby": True})
        if li.get("id")
    ]

    ordered_ids = [li["id"] for li in arc_lis]
    resolved: dict[str, dict] = {}

    for li in arc_lis:
        arc_id = li["id"]
        h2 = li.find("h2")
        title = h2.get_text(strip=True) if h2 else arc_id.replace("-", " ").title()

        groups = _parse_arc_groups(li)
        if not groups:
            continue

        group = _pick_group(groups, audio, extended)
        if not group:
            continue

        pick = _pick_resolution(group["links"], resolution)
        if not pick:
            continue

        chosen_res, chosen_url = pick
        resolved[arc_id] = {
            "arc_id": arc_id,
            "title": title,
            "resolution": chosen_res,
            "variant": group["label"],
            "kind": _kind(group),
            "pd_list_id": chosen_url.rstrip("/").split("/")[-1],
            "pd_url": chosen_url,
        }

    return ordered_ids, resolved


def fetch_arcs(resolution: str, audio: str = "subs", extended: bool = True) -> list[dict]:
    """
    Scrape ES page first; fall back to EN for any arc not available in Spanish.
    Season numbers reflect each arc's position in the merged ordered list,
    so they remain stable as new arcs are added to either page.
    """
    es_ordered, es_arcs = _scrape_page(ONEPACE_URL_ES, resolution, audio, extended)
    en_ordered, en_arcs = _scrape_page(ONEPACE_URL_EN, resolution, audio, extended)

    # Merge ordering: ES arcs first (preserving their positions), then any EN-only arcs
    seen = set(es_ordered)
    all_ordered = es_ordered + [aid for aid in en_ordered if aid not in seen]

    en_only = set(en_arcs) - set(es_arcs)
    if en_only:
        print(f"  [info] {len(en_only)} arc(s) not in ES, using EN: {', '.join(sorted(en_only))}")

    arcs = []
    for season_num, arc_id in enumerate(all_ordered, start=1):
        arc = es_arcs.get(arc_id) or en_arcs.get(arc_id)
        if arc is None:
            continue
        entry = dict(arc)
        entry["season"] = season_num
        entry["lang"] = "en" if arc_id in en_only else "es"
        entry["marker"] = f"{entry['lang']}-{entry['kind']}"
        entry["available_es"] = "1" if arc_id in es_arcs else "0"
        entry["available_en"] = "1" if arc_id in en_arcs else "0"
        if arc_id in en_only:
            entry["variant"] = f"{entry['variant']} [EN]"
        arcs.append(entry)

    return arcs


def list_pd_folder(list_id: str) -> list[dict]:
    """Return files in a pixeldrain list/folder."""
    url = f"{PIXELDRAIN_API}/list/{list_id}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("files", [])


def download_file(file_id: str, dest_path: Path, dry_run: bool = False) -> bool:
    """Download a single file from pixeldrain. Returns True if downloaded."""
    if dest_path.exists():
        print(f"  [skip] {dest_path.name} (already exists)")
        return False

    if dry_run:
        print(f"  [dry]  would download -> {dest_path}")
        return False

    url = f"{PIXELDRAIN_API}/file/{file_id}?download"
    print(f"  [dl]   {dest_path.name}", flush=True)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".part")

    try:
        with requests.get(url, headers=HEADERS, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
        tmp_path.rename(dest_path)
        print(f"  [ok]   {dest_path.name}", flush=True)
        return True
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        print(f"  [err]  {dest_path.name}: {exc}", flush=True)
        return False


def _parse_nfo_fields(nfo_path: Path) -> tuple[str, str]:
    """Return (title, plot) from an NFO file, or ('', '') on failure."""
    try:
        root = ET.parse(nfo_path).getroot()
        return root.findtext("title", "").strip(), root.findtext("plot", "").strip()
    except Exception:
        return "", ""


import xml.etree.ElementTree as ET


class PlexClient:
    """Thin Plex API client for syncing NFO metadata after downloads."""

    def __init__(self, url: str, token: str, plex_path: str, host_path: str) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._plex_path = plex_path.rstrip("/")
        self._host_path = host_path.rstrip("/")
        self._show_key: str | None = None
        self._season_keys: dict[int, str] = {}

    def _headers(self) -> dict:
        return {"X-Plex-Token": self._token}

    def _get(self, path: str) -> ET.Element:
        r = requests.get(f"{self._url}{path}", headers=self._headers(), timeout=15)
        r.raise_for_status()
        return ET.fromstring(r.text)

    def _translate(self, plex_file: str) -> Path:
        if self._plex_path and self._plex_path != self._host_path:
            return Path(plex_file.replace(self._plex_path, self._host_path, 1))
        return Path(plex_file)

    def _find_show(self, title: str) -> bool:
        try:
            for section in self._get("/library/sections").findall("Directory"):
                for show in self._get(f"/library/sections/{section.get('key')}/all").findall("Directory"):
                    if show.get("title") == title:
                        self._show_key = show.get("ratingKey")
                        return True
        except Exception as exc:
            print(f"  [plex] Could not locate show: {exc}")
        return False

    def _season_key(self, season_num: int, show_title: str) -> str | None:
        if not self._season_keys:
            if self._show_key is None and not self._find_show(show_title):
                return None
            try:
                for d in self._get(f"/library/metadata/{self._show_key}/children").findall("Directory"):
                    if d.get("index"):
                        self._season_keys[int(d.get("index"))] = d.get("ratingKey")
            except Exception as exc:
                print(f"  [plex] Could not get seasons: {exc}")
                return None
        return self._season_keys.get(season_num)

    def sync_season(self, season_num: int, season_dir: Path, show_title: str) -> None:
        key = self._season_key(season_num, show_title)
        if not key:
            return
        snfo = season_dir / "season.nfo"
        if snfo.exists():
            title, plot = _parse_nfo_fields(snfo)
            if title:
                params: dict = {"X-Plex-Token": self._token, "title.value": title, "title.locked": "1"}
                if plot:
                    params.update({"summary.value": plot, "summary.locked": "1"})
                requests.put(f"{self._url}/library/metadata/{key}", params=params, timeout=15)
                print(f"  [plex] S{season_num:02d} season synced")
        poster = season_dir / "poster.png"
        if poster.exists():
            with open(poster, "rb") as f:
                requests.post(
                    f"{self._url}/library/metadata/{key}/posters",
                    headers={**self._headers(), "Content-Type": "image/png"},
                    data=f.read(), timeout=30,
                )
            print(f"  [plex] S{season_num:02d} poster uploaded")

    def sync_episodes(self, season_num: int, season_dir: Path, show_title: str) -> None:
        key = self._season_key(season_num, show_title)
        if not key:
            return
        try:
            episodes = self._get(f"/library/metadata/{key}/children").findall("Video")
        except Exception as exc:
            print(f"  [plex] Could not get episodes for S{season_num:02d}: {exc}")
            return
        synced = 0
        for ep in episodes:
            if ep.get("guid", "").startswith("plex://"):
                continue
            part = ep.find("Media/Part")
            if part is None:
                continue
            nfo = self._translate(part.get("file", "")).with_suffix(".nfo")
            if not nfo.exists():
                continue
            title, plot = _parse_nfo_fields(nfo)
            if not title:
                continue
            params = {"X-Plex-Token": self._token, "title.value": title, "title.locked": "1"}
            if plot:
                params.update({"summary.value": plot, "summary.locked": "1"})
            requests.put(f"{self._url}/library/metadata/{ep.get('ratingKey')}", params=params, timeout=15)
            synced += 1
        if synced:
            print(f"  [plex] S{season_num:02d} {synced} episode(s) synced")


def fetch_official_seasons() -> dict[str, int]:
    """Download seasons.json from one-pace-for-plex. Returns arc_title -> official_season_num."""
    try:
        r = requests.get(f"{GITHUB_RAW}/dist/seasons.json", headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"[warn] Could not fetch seasons.json from GitHub: {exc}")
        return {}


def fetch_nfo_index() -> dict[tuple[int, int], str]:
    """
    Fetch the full GitHub repo tree in one API call.
    Returns {(official_season, episode_num): raw_download_url}.
    """
    try:
        r = requests.get(
            f"{GITHUB_API}/git/trees/main?recursive=1",
            headers=HEADERS,
            timeout=30,
        )
        r.raise_for_status()
    except Exception as exc:
        print(f"[warn] Could not fetch NFO index from GitHub: {exc}")
        return {}

    nfo_re = re.compile(r"One Pace/Season (\d+)/One Pace - S\d+E(\d+) - .+\.nfo$")
    result = {}
    for item in r.json().get("tree", []):
        m = nfo_re.match(item["path"])
        if m:
            result[(int(m.group(1)), int(m.group(2)))] = f"{GITHUB_RAW}/{quote(item['path'])}"
    return result


def _match_official_season(arc_id: str, official: dict[str, int]) -> int | None:
    """Match arc_id (URL slug, always English) against seasons.json keys slugified."""
    for k, v in official.items():
        # Drop apostrophes before slugifying (arc_ids omit them; a plain replace would insert a hyphen)
        k_clean = re.sub(r"['’]", "", k)
        slug = re.sub(r"[^a-z0-9]+", "-", k_clean.lower()).strip("-")
        if slug == arc_id or arc_id.endswith("-" + slug):
            return v
    return None


def _extract_ep_num(filename: str) -> int | None:
    """Extract episode number from a pixeldrain One Pace filename.

    Format: [One Pace][...] Arc Name NN [resolution][...].mp4
    NN may be followed by ' Extended' or similar before the resolution tag.
    """
    m = re.search(r" (\d{1,2})\s+(?:Extended\s+|Alternate\s+\S+\s+)?\[\d+p\]", filename)
    if m:
        return int(m.group(1))
    return None


def _fetch_bytes(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        return r.content if r.status_code == 200 else None
    except Exception:
        return None


def _fetch_text(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None


def _adapt_nfo_season(content: str, our_season: int) -> str:
    """Replace the <season> tag to match our folder numbering."""
    return re.sub(r"<season>\d+</season>", f"<season>{our_season}</season>", content)


def write_arc_metadata(
    arc: dict,
    season_dir: Path,
    official: dict[str, int],
    nfo_index: dict[tuple[int, int], str],
    dry_run: bool,
) -> bool:
    """Write poster, season.nfo, and per-episode NFOs for one arc. Returns True if anything was written."""
    if not season_dir.exists():
        return False

    off_season = _match_official_season(arc["arc_id"], official)
    if off_season is None:
        print(f"  [meta] No season match for '{arc['title']}', skipping NFOs")
        return False

    our_season = arc["season"]
    wrote_something = False

    # Season poster
    poster_path = season_dir / "poster.png"
    if not poster_path.exists():
        url = f"{GITHUB_RAW}/One%20Pace/season{off_season:02d}-poster.png"
        if dry_run:
            print(f"  [dry]  would download poster.png")
        elif (data := _fetch_bytes(url)):
            poster_path.write_bytes(data)
            print(f"  [meta] poster.png")
            wrote_something = True
        else:
            print(f"  [warn] Could not download poster for season {off_season}")

    # season.nfo
    snfo_path = season_dir / "season.nfo"
    if not snfo_path.exists():
        url = f"{GITHUB_RAW}/One%20Pace/Season%20{off_season}/season.nfo"
        if dry_run:
            print(f"  [dry]  would write season.nfo")
        elif (text := _fetch_text(url)):
            snfo_path.write_text(text, encoding="utf-8")
            print(f"  [meta] season.nfo")
            wrote_something = True
        else:
            print(f"  [warn] Could not download season.nfo for season {off_season}")

    # Per-episode NFOs
    video_files = sorted(list(season_dir.glob("*.mkv")) + list(season_dir.glob("*.mp4")))
    for vf in video_files:
        nfo_path = vf.with_suffix(".nfo")
        if nfo_path.exists():
            continue
        ep = _extract_ep_num(vf.name)
        if ep is None:
            print(f"  [warn] Could not parse episode number from {vf.name}")
            continue
        url = nfo_index.get((off_season, ep))
        if url is None:
            print(f"  [warn] No NFO found for S{off_season:02d}E{ep:02d} ({vf.name})")
            continue
        if dry_run:
            print(f"  [dry]  would write NFO for E{ep:02d}")
        elif (text := _fetch_text(url)):
            adapted = _adapt_nfo_season(text, our_season)
            nfo_path.write_text(adapted, encoding="utf-8")
            print(f"  [meta] E{ep:02d}.nfo -> {nfo_path.name}")
            wrote_something = True
        else:
            print(f"  [warn] Could not download NFO for E{ep:02d}")

    return wrote_something


def write_tvshow_nfo(show_dir: Path, dry_run: bool = False) -> None:
    nfo_path = show_dir / "tvshow.nfo"
    url = f"{GITHUB_RAW}/One%20Pace/tvshow.nfo"
    if dry_run:
        print(f"  [dry]  would update tvshow.nfo")
        return
    if (text := _fetch_text(url)):
        text = re.sub(r"<originaltitle>.*?</originaltitle>", "<originaltitle>One Pace</originaltitle>", text)
        nfo_path.write_text(text, encoding="utf-8")
        print(f"[meta] tvshow.nfo updated from GitHub")
    elif not nfo_path.exists():
        nfo_path.write_text(TVSHOW_NFO, encoding="utf-8")
        print(f"[meta] tvshow.nfo written (fallback)")


def main() -> None:
    parser = argparse.ArgumentParser(description="One Pace Jellyfin downloader")
    parser.add_argument(
        "--resolution", default="1080p",
        choices=RESOLUTIONS,
        help="Preferred resolution, falls back to next best (default: 1080p)",
    )
    parser.add_argument(
        "--audio", default="subs",
        choices=["subs", "dub"],
        help="What to take: subs=original audio with subtitles, dub=a dubbed track. "
             "Never crosses over, so 'subs' falls back to English subtitles rather than "
             "to a Spanish dub (default: subs)",
    )
    parser.add_argument(
        "--no-extended", dest="extended", action="store_false", default=True,
        help="Skip Extended Cut even when available (default: prefer Extended Cut)",
    )
    parser.add_argument(
        "--output", default="/mnt/data/series",
        help="Root media directory (default: /mnt/data/series)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be downloaded without downloading",
    )
    parser.add_argument(
        "--arc", default=None,
        help="Download this arc + the next one (by id, e.g. 'skypiea')",
    )
    parser.add_argument(
        "--list-arcs", action="store_true",
        help="List all arcs and exit",
    )
    parser.add_argument(
        "--pushgateway", default=None,
        metavar="URL",
        help="Prometheus Pushgateway URL (e.g. http://pushgateway:9091)",
    )
    parser.add_argument(
        "--no-metadata", dest="metadata", action="store_false", default=True,
        help="Skip fetching NFOs, posters, and season metadata from GitHub",
    )
    parser.add_argument(
        "--plex-url", default=None, metavar="URL",
        help="Plex server URL for automatic metadata sync (e.g. http://localhost:32400)",
    )
    parser.add_argument(
        "--plex-token", default=None, metavar="TOKEN",
        help="Plex API token",
    )
    parser.add_argument(
        "--plex-path", default=None, metavar="PATH",
        help="Media root path as seen by Plex (default: same as --output). "
             "Set this if Plex runs in a container with a different mount path, "
             "e.g. /data/series when --output is /mnt/data/series",
    )
    args = parser.parse_args()

    plex: PlexClient | None = None
    if args.plex_url and args.plex_token:
        plex = PlexClient(
            url=args.plex_url,
            token=args.plex_token,
            plex_path=args.plex_path or args.output,
            host_path=args.output,
        )
    elif args.plex_url or args.plex_token:
        print("[warn] Both --plex-url and --plex-token are required for Plex sync")

    check_connectivity()
    arcs = fetch_arcs(args.resolution, audio=args.audio, extended=args.extended)
    canon_arcs = fetch_canon_arcs()

    official_seasons: dict[str, int] = {}
    nfo_index: dict[tuple[int, int], str] = {}
    if args.metadata and not args.list_arcs:
        print("Fetching metadata index from GitHub...")
        official_seasons = fetch_official_seasons()
        nfo_index = fetch_nfo_index()
        print(f"  {len(nfo_index)} episode NFOs indexed")

    if args.list_arcs:
        print(f"\n{'S#':>4}  {'Arc ID':<35} {'Title':<30} {'Res':<6} {'Variant'}")
        print("-" * 100)
        for arc in arcs:
            print(f"S{arc['season']:02d}   {arc['arc_id']:<35} {arc['title']:<30} {arc['resolution']:<6} {arc['variant']}")
        return

    if args.arc:
        try:
            idx = next(i for i, a in enumerate(arcs) if a["arc_id"] == args.arc)
        except StopIteration:
            print(f"Arc '{args.arc}' not found. Use --list-arcs to see available arcs.")
            sys.exit(1)
        arcs = arcs[idx:idx + 2]

    output_root = Path(args.output)
    show_dir = output_root / SHOW_DIR_NAME

    if not args.dry_run:
        show_dir.mkdir(parents=True, exist_ok=True)
        write_tvshow_nfo(show_dir, dry_run=args.dry_run)

    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "arcs_done": 0, "arcs_total": len(arcs)}
    push_metrics(args.pushgateway, stats)

    for arc in arcs:
        season_dir = show_dir / f"Season {arc['season']:02d}"

        print(f"\n=== S{arc['season']:02d} {arc['title']} [{arc['resolution']}] — {arc['variant']} ===")
        print(f"    pixeldrain folder: {arc['pd_list_id']}")

        try:
            files = list_pd_folder(arc["pd_list_id"])
        except Exception as exc:
            print(f"  [err] Could not list folder {arc['pd_list_id']}: {exc}")
            stats["arcs_done"] += 1
            push_metrics(args.pushgateway, stats)
            continue

        if not files:
            print("  [warn] Empty folder")
            stats["arcs_done"] += 1
            push_metrics(args.pushgateway, stats)
            continue

        extras = [f for f in files if Path(f["name"]).suffix.lower() not in VIDEO_SUFFIXES]
        files = [f for f in files if Path(f["name"]).suffix.lower() in VIDEO_SUFFIXES]
        if extras:
            print(f"  {len(extras)} non-video file(s) ignored: "
                  f"{', '.join(f['name'] for f in extras[:3])}")

        print(f"  {len(files)} file(s) in folder")

        # The replacement decision needs the source listing above, because a source with
        # fewer episodes than the season already holds cannot replace it. The Spanish side
        # of an arc still in progress publishes one episode where the English side has five.
        stored = _read_lang_marker(season_dir)
        disagrees = _disk_disagrees(season_dir, arc["marker"])
        on_disk = len(_season_videos(season_dir))
        wants_replacing = _needs_replacing(stored, arc["marker"]) or disagrees
        if wants_replacing and len(files) < on_disk:
            print(f"  [keep]  {on_disk} file(s) on disk, this source has only "
                  f"{len(files)}: leaving the season alone")
        elif _needs_replacing(stored, arc["marker"]):
            print(f"  [replace] marker says {stored}, wanted as {arc['marker']}")
            _clear_season(season_dir, f"{stored} replaced by {arc['marker']}", args.dry_run)
        elif disagrees:
            audio, subtitles = disagrees
            print(f"  [replace] files are {audio} audio / {subtitles} subs, "
                  f"wanted as {arc['marker']}")
            _clear_season(season_dir, f"{audio}/{subtitles} replaced by {arc['marker']}",
                          args.dry_run)

        if args.dry_run:
            for f in files:
                download_file(f["id"], season_dir / f["name"], dry_run=True)
        else:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {
                    pool.submit(download_file, f["id"], season_dir / f["name"]): f
                    for f in files
                }
                for future in as_completed(futures):
                    dest = season_dir / futures[future]["name"]
                    ok = future.result()
                    if ok:
                        stats["downloaded"] += 1
                    elif dest.exists():
                        stats["skipped"] += 1
                    else:
                        stats["failed"] += 1

        if not args.dry_run:
            _write_lang_marker(season_dir, arc["marker"])
            push_arc_metrics(args.pushgateway, arcs, show_dir, canon_arcs)

        arc_new = stats["downloaded"] - stats.get("_prev_downloaded", 0)
        stats["_prev_downloaded"] = stats["downloaded"]

        wrote_meta = False
        if args.metadata:
            wrote_meta = write_arc_metadata(arc, season_dir, official_seasons, nfo_index, args.dry_run)

        if plex and not args.dry_run and (arc_new > 0 or wrote_meta):
            plex.sync_season(arc["season"], season_dir, SHOW_DIR_NAME)
            plex.sync_episodes(arc["season"], season_dir, SHOW_DIR_NAME)

        stats["arcs_done"] += 1
        push_metrics(args.pushgateway, stats)
    print(f"\nDone. {stats['downloaded']} new, {stats['skipped']} skipped, {stats['failed']} failed.")


if __name__ == "__main__":
    main()
