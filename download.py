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
import re
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ONEPACE_URL_ES = "https://onepace.net/es/watch"
ONEPACE_URL_EN = "https://onepace.net/en/watch"
PIXELDRAIN_API = "https://pixeldrain.com/api"
SHOW_DIR_NAME = "One Pace"
RESOLUTIONS = ["1080p", "720p", "480p"]
LANG_MARKER = ".lang"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


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


def push_arc_metrics(pgw_url: str, arcs: list[dict], show_dir: Path) -> None:
    """Push per-arc status metrics to Pushgateway under a separate grouping key."""
    if not pgw_url:
        return
    lines = ["# TYPE onepace_arc_episodes_on_disk gauge"]
    for arc in arcs:
        season_dir = show_dir / f"Season {arc['season']:02d}"
        on_disk = len(list(season_dir.glob("*.mkv"))) if season_dir.exists() else 0
        lang_on_disk = _read_lang_marker(season_dir) or "none"
        labels = (
            f'arc_id="{_escape_label(arc["arc_id"])}",'
            f'title="{_escape_label(arc["title"])}",'
            f'season="{arc["season"]:02d}",'
            f'available_es="{arc["available_es"]}",'
            f'available_en="{arc["available_en"]}",'
            f'lang="{lang_on_disk}"'
        )
        lines.append(f"onepace_arc_episodes_on_disk{{{labels}}} {on_disk}")
    payload = "\n".join(lines) + "\n"
    try:
        requests.post(
            f"{pgw_url.rstrip('/')}/metrics/job/one-pace-downloader/type/arc-status",
            data=payload,
            headers={"Content-Type": "text/plain"},
            timeout=5,
        )
    except Exception as exc:
        print(f"  [warn] Pushgateway arc metrics unreachable: {exc}")


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


def _pick_group(groups: list[dict], audio: str, extended: bool) -> dict | None:
    """
    Choose the best group given audio preference and extended preference.
    audio: 'subs' prefer subtitles, fallback to dub if unavailable
           'dub'  prefer dub, fallback to subs if unavailable
    extended: True  = prefer Extended Cut over regular when available
              False = prefer regular, ignore Extended Cut
    Alternate Cut (e.g. G-8) is never picked automatically.
    Handles both Spanish labels (subtitulo/doblaje) and English labels (subtitle/dub).
    """
    def lower(g): return g["label"].lower()
    is_subs     = lambda g: "subtitulo" in lower(g) or "subtitle" in lower(g)
    is_dub      = lambda g: "doblaje" in lower(g) or ("dub" in lower(g) and "subtitle" not in lower(g))
    is_extended = lambda g: "extended cut" in lower(g)
    is_alternate = lambda g: "alternate cut" in lower(g)
    is_regular  = lambda g: not is_extended(g) and not is_alternate(g)

    subs_groups = [g for g in groups if is_subs(g) and not is_alternate(g)]
    dub_groups  = [g for g in groups if is_dub(g)]
    primary     = subs_groups if audio == "subs" else dub_groups
    secondary   = dub_groups  if audio == "subs" else subs_groups

    def best(candidates):
        if not candidates:
            return None
        if extended:
            return next(
                (g for g in candidates if is_extended(g)),
                next((g for g in candidates if is_regular(g)), candidates[0]),
            )
        return next((g for g in candidates if is_regular(g)), candidates[0])

    return best(primary) or best(secondary)


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


def _read_lang_marker(season_dir: Path) -> str | None:
    p = season_dir / LANG_MARKER
    return p.read_text().strip() if p.exists() else None


def _write_lang_marker(season_dir: Path, lang: str) -> None:
    season_dir.mkdir(parents=True, exist_ok=True)
    (season_dir / LANG_MARKER).write_text(lang)


def _upgrade_season_to_es(season_dir: Path, dry_run: bool) -> None:
    """Delete EN fallback MKV files so the ES versions can be downloaded fresh."""
    mkv_files = list(season_dir.glob("*.mkv"))
    if dry_run:
        print(f"  [dry]  would replace {len(mkv_files)} EN file(s) with ES version")
        return
    for f in mkv_files:
        f.unlink()
        print(f"  [del]  {f.name} (EN fallback replaced by ES)")
    (season_dir / LANG_MARKER).unlink(missing_ok=True)


def write_tvshow_nfo(show_dir: Path) -> None:
    nfo_path = show_dir / "tvshow.nfo"
    if not nfo_path.exists():
        nfo_path.write_text(TVSHOW_NFO, encoding="utf-8")
        print(f"Created {nfo_path}")


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
        help="Audio preference: subs=subtitles/subtitulos, dub=dub/doblaje (default: subs)",
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
    args = parser.parse_args()

    check_connectivity()
    arcs = fetch_arcs(args.resolution, audio=args.audio, extended=args.extended)

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
        write_tvshow_nfo(show_dir)

    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "arcs_done": 0, "arcs_total": len(arcs)}
    push_metrics(args.pushgateway, stats)

    for arc in arcs:
        season_dir = show_dir / f"Season {arc['season']:02d}"
        arc_lang = arc["lang"]

        stored_lang = _read_lang_marker(season_dir)
        if stored_lang == "en" and arc_lang == "es":
            print(f"\n[upgrade] S{arc['season']:02d} {arc['title']}: ES now available, replacing EN files")
            _upgrade_season_to_es(season_dir, args.dry_run)

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

        print(f"  {len(files)} file(s) in folder")

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
            _write_lang_marker(season_dir, arc_lang)

        stats["arcs_done"] += 1
        push_metrics(args.pushgateway, stats)

    push_arc_metrics(args.pushgateway, arcs, show_dir)
    print(f"\nDone. {stats['downloaded']} new, {stats['skipped']} skipped, {stats['failed']} failed.")


if __name__ == "__main__":
    main()
