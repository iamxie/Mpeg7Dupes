# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
render_report.py
================

Turns a `find_reuse.py --json` record into a static HTML table you can open in
a browser, or hand to somebody else.

    uv run tools/find_reuse.py --source ./source --candidates ./comp \\
        --json reuse.json
    uv run tools/render_report.py reuse.json --out report.html

One row per match, with both players already parked a few seconds before the
part that matters: theirs before the point where the reuse starts, yours before
the part that was taken. Pressing play on either shows the cut happening rather
than making the reader hunt for it.

Paths
-----
Schema 5 records the original scan directory in path_base. Paths are resolved
there and translated relative to the output HTML, while the displayed paths
stay as supplied. Legacy records can use --path-base; without it their old
report-folder resolution is kept with a warning. Nothing is copied or
re-encoded. Missing files are named so blank players are explained.

What the page deliberately does not say
---------------------------------------
There is no percentage anywhere. The comparison over-reports how much of a
source was used, by however far the walk carries past each end of the shared
region, so a figure like "88%" would be quoted back as exact when it is not.
The page gives the two durations and the matched length instead and lets the
reader form the ratio, which is the same information without the false
precision.
"""

import argparse
import html
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

# Sibling data module, same arrangement as find_reuse.py's own import of
# detect_bars: both live in tools/, and Python puts the script's directory on
# the path. Sharing the phrasing matters more than the few lines it saves —
# the terminal and the report must not describe one match two ways — and a
# data module is all that takes, rather than loading the scanner.
try:
    from reuse_record import (SCHEMA, READABLE_SCHEMAS, FAILED, SKIPPED, NOT_COMPARED,
                              LIMITS, as_clock, where_of, crop_description, video_warnings)
except ImportError:
    sys.exit("render_report.py needs reuse_record.py beside it in tools/")

# Seconds of run-up before the match, so the reader sees the join rather than
# landing on top of it.
LEAD_IN = 5.0

STYLE = """
:root { color-scheme: light; }
body { margin: 0; padding: 2rem 1.5rem 4rem;
       font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI",
             "Noto Sans TC", "Microsoft JhengHei", sans-serif;
       color: #1a1a1a; background: #fbfbfa; }
main { max-width: 1180px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .3rem; }
.sub { color: #666; margin: 0 0 1.6rem; font-size: .9rem; }
.method { background: #fff; border: 1px solid #e3e3e0; border-radius: 6px;
          padding: 1rem 1.2rem; margin-bottom: 1.8rem; font-size: .87rem; }
.method h2 { font-size: .8rem; margin: 0 0 .6rem; text-transform: uppercase;
             letter-spacing: .06em; color: #666; font-weight: 600; }
.method dl { display: grid; grid-template-columns: max-content 1fr;
             gap: .3rem 1.2rem; margin: 0; }
.method dt { color: #666; }
.method dd { margin: 0; font-variant-numeric: tabular-nums; }
.method p { margin: .9rem 0 0; color: #555; }
table { border-collapse: collapse; width: 100%; background: #fff;
        border: 1px solid #e3e3e0; border-radius: 6px; }
th { text-align: left; font-size: .75rem; text-transform: uppercase;
     letter-spacing: .06em; color: #666; font-weight: 600;
     padding: .7rem .9rem; border-bottom: 1px solid #e3e3e0; }
td { padding: .9rem; border-bottom: 1px solid #f0f0ee; vertical-align: top; }
tr:last-child td { border-bottom: 0; }
video { width: 100%; max-width: 320px; background: #000; border-radius: 4px;
        display: block; }
.name { font-size: .82rem; margin-top: .45rem; word-break: break-all; }
.path { color: #888; font-size: .76rem; word-break: break-all; }
.when { font-weight: 600; font-variant-numeric: tabular-nums; }
.hint { color: #777; font-size: .78rem; margin-top: .3rem; }
.facts { list-style: none; margin: 0; padding: 0; font-size: .84rem; }
.facts li { margin-bottom: .25rem; }
.facts b { font-weight: 600; font-variant-numeric: tabular-nums; }
.warn { color: #8a4b00; background: #fff6e8; border: 1px solid #f0d9b5;
        border-radius: 6px; padding: .8rem 1rem; margin-bottom: 1.5rem;
        font-size: .86rem; }
.warn code { font-size: .8rem; }
.empty { color: #666; padding: 2rem; text-align: center; background: #fff;
         border: 1px solid #e3e3e0; border-radius: 6px; }
"""


def media_src(path: str, at: float = 0.0, *, native: bool = False) -> str:
    """A src that resolves from the report's folder and opens at `at` seconds.

    #t= is a media fragment, which browsers honour on file:// as well as over
    http, so the playhead lands without a line of script.
    """
    # Resolved paths use the host filesystem's spelling. A backslash in a
    # Linux filename is literal; only legacy un-resolved paths use the old
    # Windows separator conversion. Encode colons to avoid a URI scheme.
    url = quote(path if native else path.replace("\\", "/"), safe="/" if native else "/:")
    return f"{url}#t={max(0.0, at):.0f}" if at > 0 else url


def bars_lines(match: dict) -> str:
    """What was cropped off each side before the comparison, if anything.

    Both sides are detected and cropped, so both have to be reported. Naming
    only theirs reads as "no bars here" on a pair where yours is the one that
    has them, and bars are worth stating outright: they are a way of dodging
    detection, and cropping them is why a copy that has them still lines up
    with one that does not.
    """
    lines = []
    for whose, side in (("yours", "source"), ("theirs", "candidate")):
        crop = match.get(side + "_crop", "")
        state = match.get(side + "_crop_state", "detected" if crop else "unknown")
        description = crop_description({"crop": crop, "crop_state": state,
                                        "crop_mode": match.get(side + "_crop_mode")})
        label = "View on " if state == "fixed" else "Bars on "
        lines.append(f'<li>{label}{whose}: {html.escape(description.removeprefix("Bars: "))}</li>')
    return "".join(lines)


def duration_cell(match: dict) -> str:
    """The fourth column: the two lengths, the matched length, and the bars.

    The matched length is the raw count the comparison reported. On an
    overrun that is longer than the shorter video, which is exactly what the
    reader has to see; the record says why beside it.
    """
    matched = match["matched_seconds"]
    overrun = (f'<li>Overrun: {html.escape(match["overrun_reason"])}</li>'
               if match.get("overrun_reason") else "")
    speed = ("" if match.get("framerateratio", 1.0) == 1.0 else
             f'<li>Speed ratio <b>{match["framerateratio"]:.2f}</b>: the '
             f'coverage counts the slower clip\'s frames; positions have '
             f'additional speed-grid error and need visual verification</li>')
    return (
        '<ul class="facts">'
        + ('<li><b>Needs review: 5% crop fallback</b>. Compared '
           + html.escape(match['source_view']) + ' source / '
           + html.escape(match['candidate_view']) + ' candidate. Both directional '
           'measurements are retained in the JSON.</li>' if match.get('requires_review') else '') +
        f'<li>Source length <b>{as_clock(match["source_seconds"])}</b></li>'
        f'<li>Their video <b>{as_clock(match["candidate_seconds"])}</b></li>'
        f'<li>Matched <b>{as_clock(matched)}</b> ({matched:.0f} s, '
        f'{match["matchframes"]} frames)</li>'
        f'{overrun}{speed}{bars_lines(match)}'
        '</ul>')


def player(path: str, at: float, name: str, shown_path: str, *, native: bool = False) -> str:
    """One video cell, parked `at` seconds in.

    data-start as well as the #t= fragment. Chromium ignores the fragment on
    an element that preloads only metadata, which is every player on this page,
    so the script at the end of the document does the seek and the fragment
    stays for the browsers that honour it and for anyone reading the source.
    """
    seek = f' data-start="{at:.0f}"' if at > 0 else ""
    lead = ("" if not at else
            f'<div class="hint">opens at {as_clock(at)}, '
            f'{LEAD_IN:.0f}s before it</div>')
    return (f'<video src="{media_src(path, at, native=native)}"{seek} controls '
            f'preload="metadata"></video>'
            f'<div class="name">{html.escape(name)}</div>'
            f'<div class="path">{html.escape(shown_path)}</div>{lead}')


def row(match: dict) -> str:
    """One match as a table row."""
    # A position that overran describes a region that is not there, so both
    # players open at the beginning rather than parking on a made-up timestamp.
    def before(seconds):
        return 0.0 if match["overrun"] else max(0.0, seconds - LEAD_IN)

    # Both sides are measured now, so both can be parked: theirs just before
    # the join, and ours just before the part that was taken.
    mine = player(match.get("source_media", match["source_path"]), before(match["source_begin_seconds"]),
                  match["source"], match["source_path"], native="source_media" in match)
    theirs = player(match.get("candidate_media", match["candidate_path"]), before(match["start_seconds"]),
                    match["candidate"], match["candidate_path"], native="candidate_media" in match)
    span = ("" if match["overrun"] else
            f'<div class="hint">runs to {as_clock(match["end_seconds"])}, '
            f'taken from {as_clock(match["source_begin_seconds"])}'
            f'–{as_clock(match["source_end_seconds"])} of yours</div>')
    return f"""    <tr>
      <td>{mine}</td>
      <td>{theirs}</td>
      <td><div class="when">{html.escape(where_of(match))}</div>{span}</td>
      <td>{duration_cell(match)}</td>
    </tr>"""


def unprocessed(record: dict) -> str:
    """The files that were asked about and not compared: failed ones with the
    stage and reason, skipped ones with theirs. Shown, not dropped: a page
    that listed only the matches would read as a complete run."""
    rows = [(r, "source") for r in record["sources"]] + \
           [(r, "candidate") for r in record["candidates"]]
    items = []
    for video, role in rows:
        if video["status"] == FAILED:
            items.append(f'<li>{html.escape(video["path"])} ({role}): '
                         f'{html.escape(video["failure"]["stage"])}, '
                         f'<code>{html.escape(video["failure"]["reason"])}</code></li>')
        elif video["status"] == NOT_COMPARED:
            items.append(f'<li>{html.escape(video["path"])} ({role}): not compared, '
                         f'{html.escape(video.get("reason", ""))}</li>')
        elif video["status"] == SKIPPED:
            items.append(f'<li>{html.escape(video["path"])} ({role}): skipped, '
                         f'{html.escape(video.get("reason", ""))}</li>')
    compared = record.get("comparison", {}).get("sources_completed", [])
    if items and compared and not record.get("comparison", {}).get("complete", True):
        items.append('<li>Compared against these sources only: '
                     + ', '.join(html.escape(path) for path in compared) + '</li>')
    if not items:
        return ""
    failed = sum(1 for v, _ in rows if v["status"] == FAILED)
    lead = (f'<b>{failed} file{"s" if failed != 1 else ""} could not be '
            f'processed, so this run is incomplete.</b> ' if failed else
            '<b>Not every requested file was compared.</b> ')
    return f'<div class="warn">{lead}<ul>{"".join(items)}</ul></div>'


def video_inventory(record: dict) -> str:
    """Keep per-video decisions visible even when there are no matches."""
    items = []
    for role, videos in (("source", record["sources"]), ("candidate", record["candidates"])):
        for video in videos:
            if "crop_state" not in video and "crop" not in video:
                continue
            warnings = video_warnings(video, role)
            detail = " ".join(w["message"] for w in warnings)
            fallback = video.get("crop_fallback")
            if fallback:
                detail += (" Fixed view: " + crop_description(fallback) if fallback["status"] == "processed"
                           else " Fixed view failed: " + fallback["failure"]["reason"])
            status = "no match reaching the threshold" if video["status"] == "checked" else video["status"]
            items.append(f'<li>{html.escape(video["path"])} ({role}, {html.escape(status)}): '
                         f'{html.escape(crop_description(video))}'
                         f'<div class="hint">{html.escape(detail)}</div></li>')
    return '<details class="method"><summary>Video decisions and warnings</summary><ul>' + "".join(items) + '</ul></details>' if items else ""


def render(record: dict, missing: list[str], media_paths: dict | None = None) -> str:
    """The whole page. It shows what the record says and decides nothing:
    which candidates matched was settled by find_reuse.py at its threshold."""
    settings = record["settings"]
    videos = {v["path"]: v for v in record["sources"] + record["candidates"]}
    matches = []
    for original in record["matches"]:
        match = dict(original)
        for side in ("source", "candidate"):
            video = videos.get(match[side + "_path"], {})
            crop = (match.get(side + "_crop", "") if side + "_view" in match
                    else video.get("crop", match.get(side + "_crop", "")))
            match[side + "_crop"] = crop
            match[side + "_crop_state"] = ("fixed" if match.get(side + "_view") == "crop5"
                                            else video.get("crop_state", "detected" if crop else "unknown"))
            match[side + "_crop_mode"] = video.get("crop_mode", "unknown")
            if media_paths:
                match[side + "_media"] = media_paths[match[side + "_path"]]
        matches.append(match)
    summary = record.get("summary") or {}
    sources = summary.get("sources_requested", len(record["sources"]))
    candidates = summary.get("candidates_requested", len(record["candidates"]))
    counts = (f'{len(matches)} match'
              f'{"es" if len(matches) != 1 else ""} '
              f'from {sources} source'
              f'{"s" if sources != 1 else ""} '
              f'against {candidates} candidate'
              f'{"s" if candidates != 1 else ""}')
    if summary and not summary.get("complete", True):
        counts += (f', {summary["sources_failed"] + summary["candidates_failed"] + summary.get("sources_not_compared", 0) + summary.get("candidates_not_compared", 0)}'
                   f' not processed')

    warning = unprocessed(record)
    reviews = sum(bool(m.get("requires_review")) for m in matches)
    if reviews:
        counts += f', {reviews} need review (crop fallback)'
        warning += '<div class="warn"><b>Needs review</b>: ' + html.escape(LIMITS['crop_fallback']) + '</div>'
    if record.get("error"):
        warning += '<div class="warn">' + html.escape(record["error"]["reason"]) + '</div>'
    if missing:
        listed = "".join(f"<li>{html.escape(p)}</li>" for p in missing[:12])
        more = (f"<li>and {len(missing) - 12} more</li>" if len(missing) > 12
                else "")
        warning += (f'<div class="warn"><b>These videos are not where this '
                    f'report expects them.</b> Their players will stay blank. '
                    f'Restore the files at their recorded locations, or scan '
                    f'their new locations.<ul>{listed}{more}</ul>'
                    f'</div>')

    if matches:
        body = f"""<table>
    <tr><th>My video</th><th>Their video</th><th>Reuse starts</th>
        <th>Details</th></tr>
{chr(10).join(row(m) for m in matches)}
  </table>"""
    else:
        complete = summary.get("complete", True) and record.get("comparison", {}).get("complete", True)
        message = (f'No candidate reached the {settings["min_coverage"]:.0f}% threshold; reuse is not ruled out.'
                   if complete else 'No completed match to show. Comparisons are incomplete; see the file statuses above.')
        body = f'<div class="empty">{message}</div>'

    # Current interpretation warnings also apply to older readable records.
    limits = dict(record.get("limits", {}))
    limits.update(LIMITS)
    caveats = '<ul>' + ''.join(f'<li>{html.escape(value)}</li>' for value in limits.values()) + '</ul>'
    flags = html.escape(' '.join(settings.get("comparison_args", [])) or 'not recorded (legacy record)')

    tool = record.get("tool", {}).get("mpeg7dupes") or "unknown build"
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Reuse report</title>
<style>{STYLE}</style>
<main>
  <h1>Reuse report</h1>
  <p class="sub">{html.escape(counts)} &middot; generated
     {html.escape(record["generated_at"])}</p>
  {warning}
  <div class="method">
    <h2>How this was measured</h2>
    <dl>
      <dt>Method</dt><dd>MPEG-7 video signature, compared frame by frame</dd>
      <dt>Sampling</dt><dd>{settings["fps"]:g} fps</dd>
      <dt>Frame threshold</dt><dd>-x {settings["thxh"]}</dd>
      <dt>Search mode</dt><dd>{html.escape(settings["mode"])}</dd>
      <dt>Report threshold</dt>
        <dd>{settings["min_coverage"]:.0f}% of the source</dd>
      <dt>Bar cropping</dt>
        <dd>{html.escape(settings.get("crop_mode", "motion") + " mode") if settings["crop_bars"] else "off"}</dd>
      <dt>Coarse filter</dt><dd>{"off, -d 10001" if settings.get("coarse_filter") is False else "on"}</dd>
      <dt>Fixed 5% fallback</dt><dd>{"on; additional hits need review" if settings.get("crop_fallback") else "off"}</dd>
      <dt>Comparison built</dt><dd>{html.escape(tool)}</dd>
      <dt>Comparison arguments</dt><dd><code>{flags}</code></dd>
    </dl>
    <p>An excerpt shorter than about {50 / settings["fps"]:g} seconds of source
       is found only partly or not at all at this sampling rate.</p>
    <p>Low-motion or repetitive scenes can place a match far from its true
       location, even at ratio 1.0. Short sources can match unrelated footage
       with simple light/dark layouts. A miss does not rule out reuse.</p>
    <details><summary>Limits and interpretation</summary>{caveats}</details>
  </div>
  {body}
  {video_inventory(record)}
</main>
<script>
// Park each player before the match it is showing. The #t= fragment on the
// src says the same thing, but Chromium ignores it when the element preloads
// only metadata, and every player here does. Guarded on currentTime so a
// reader who has already scrubbed does not get yanked back when metadata for
// a player further down the page finally arrives.
for (const video of document.querySelectorAll("video[data-start]")) {{
  const at = parseFloat(video.dataset.start);
  const seek = () => {{ if (video.currentTime < 0.1) video.currentTime = at; }};
  if (video.readyState >= 1) seek();
  else video.addEventListener("loadedmetadata", seek, {{once: true}});
}}
</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a find_reuse.py JSON record as a static HTML table.")
    parser.add_argument("record", metavar="JSON",
                        help="The file written by find_reuse.py --json.")
    parser.add_argument("--out", metavar="FILE", default="report.html",
                        help="Where to write the page. Default report.html.")
    parser.add_argument("--path-base", metavar="DIR",
                        help="Original scan folder, for legacy records without path_base, "
                             "or to explicitly relocate relative video paths.")
    args = parser.parse_args()

    try:
        record = json.loads(Path(args.record).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"cannot read {args.record}: {exc}")

    # Refuse an unknown schema rather than guess at it. Every field here is a
    # measurement, and a misread one is worse than a missing one.
    if record.get("schema") not in READABLE_SCHEMAS:
        sys.exit(f"{args.record} is schema {record.get('schema')!r}, "
                 f"this script reads {SCHEMA!r}")

    out = Path(args.out)
    # Legacy records did not store cwd; keep their old output-folder fallback
    # with a visible warning, or let the caller supply the original scan base.
    base = Path(args.path_base or record.get("path_base") or out.parent).resolve()
    if not args.path_base and not record.get("path_base"):
        print("legacy record has no path_base; assuming the report folder. "
              "Use --path-base for the original scan folder.", file=sys.stderr)
    referenced = {m["source_path"] for m in record["matches"]}
    referenced |= {m["candidate_path"] for m in record["matches"]}
    missing = sorted(p for p in referenced if not (base / p).exists())

    media_paths = {p: os.path.relpath((base / p).resolve(), out.parent.resolve())
                   for p in referenced}
    out.write_text(render(record, missing, media_paths))
    print(f"wrote {out}")
    if missing:
        print(f"{len(missing)} of {len(referenced)} videos do not resolve from "
              f"{base}/, so their players will be blank. Restore the files, or "
              f"supply --path-base if the video tree moved.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
