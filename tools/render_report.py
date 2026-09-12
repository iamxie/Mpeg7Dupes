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

Paths, and why the report has to sit where it does
--------------------------------------------------
The record stores each video by the path find_reuse.py walked, so a report
written beside the folders it named resolves them and one written elsewhere
does not. Nothing is copied or re-encoded: a browser plays the original file
off disk. This script checks every path it emits and says which ones will not
resolve, rather than producing a page of broken players.

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
import sys
from pathlib import Path
from urllib.parse import quote

# Sibling data module, same arrangement as find_reuse.py's own import of
# detect_bars: both live in tools/, and Python puts the script's directory on
# the path. Sharing the phrasing matters more than the few lines it saves —
# the terminal and the report must not describe one match two ways — and a
# data module is all that takes, rather than loading the scanner.
try:
    from reuse_record import SCHEMA, READABLE_SCHEMAS, FAILED, SKIPPED, NOT_COMPARED, as_clock, where_of
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


def media_src(path: str, at: float = 0.0) -> str:
    """A src that resolves from the report's folder and opens at `at` seconds.

    #t= is a media fragment, which browsers honour on file:// as well as over
    http, so the playhead lands without a line of script.
    """
    url = quote(path.replace("\\", "/"), safe="/:")
    return f"{url}#t={max(0.0, at):.0f}" if at > 0 else url


def bars_lines(match: dict) -> str:
    """What was cropped off each side before the comparison, if anything.

    Both sides are detected and cropped, so both have to be reported. Naming
    only theirs reads as "no bars here" on a pair where yours is the one that
    has them, and bars are worth stating outright: they are a way of dodging
    detection, and cropping them is why a copy that has them still lines up
    with one that does not.
    """
    sides = [("yours", match["source_crop"]), ("theirs", match["candidate_crop"])]
    found = [(whose, crop) for whose, crop in sides if crop]
    if not found:
        return "<li>Bars: none detected on either video</li>"
    return "".join(
        f'<li>Bars on {whose}: cropped before comparing, '
        f'<code>{html.escape(crop)}</code></li>' for whose, crop in found)


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
             f'coverage counts the slower clip\'s frames, the positions hold '
             f'to within that ratio\'s grid</li>')
    return (
        '<ul class="facts">'
        f'<li>Source length <b>{as_clock(match["source_seconds"])}</b></li>'
        f'<li>Their video <b>{as_clock(match["candidate_seconds"])}</b></li>'
        f'<li>Matched <b>{as_clock(matched)}</b> ({matched:.0f} s, '
        f'{match["matchframes"]} frames)</li>'
        f'{overrun}{speed}{bars_lines(match)}'
        '</ul>')


def player(path: str, at: float, name: str, shown_path: str) -> str:
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
    return (f'<video src="{media_src(path, at)}"{seek} controls '
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
    mine = player(match["source_path"], before(match["source_begin_seconds"]),
                  match["source"], match["source_path"])
    theirs = player(match["candidate_path"], before(match["start_seconds"]),
                    match["candidate"], match["candidate_path"])
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


def render(record: dict, missing: list[str]) -> str:
    """The whole page. It shows what the record says and decides nothing:
    which candidates matched was settled by find_reuse.py at its threshold."""
    settings, matches = record["settings"], record["matches"]
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
    if record.get("error"):
        warning += '<div class="warn">' + html.escape(record["error"]["reason"]) + '</div>'
    if missing:
        listed = "".join(f"<li>{html.escape(p)}</li>" for p in missing[:12])
        more = (f"<li>and {len(missing) - 12} more</li>" if len(missing) > 12
                else "")
        warning += (f'<div class="warn"><b>These videos are not where this '
                    f'report expects them.</b> Their players will stay blank. '
                    f'Put the report in the folder the paths are relative to, '
                    f'or rerun find_reuse.py from there.<ul>{listed}{more}</ul>'
                    f'</div>')

    if matches:
        body = f"""<table>
    <tr><th>My video</th><th>Their video</th><th>Reuse starts</th>
        <th>Details</th></tr>
{chr(10).join(row(m) for m in matches)}
  </table>"""
    else:
        body = ('<div class="empty">No candidate reached the '
                f'{settings["min_coverage"]:.0f}% threshold.</div>')

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
        <dd>{"on" if settings["crop_bars"] else "off"}</dd>
      <dt>Coarse filter</dt><dd>{"off, -d 10001" if settings.get("coarse_filter") is False else "on"}</dd>
      <dt>Comparison built</dt><dd>{html.escape(tool)}</dd>
    </dl>
    <p>Matching is on what the frames look like, not on the file contents, so
       re-encoding, rescaling and trimming do not hide it. Each start and end
       is the first and the last frame the comparison accepted, at
       {settings["fps"]:g} samples a second, so a boundary can sit a frame or
       two from the cut. One match is shown per pair, the longest run found;
       a source reused in several separate places is shown by the longest of
       them. An excerpt shorter than about {50 / settings["fps"]:.0f} seconds
       of source is found only partly or not at all at this sampling rate.
       Coverage here is against the length of the source: how much of your
       video their video holds.</p>
  </div>
  {body}
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
                        help="Where to write the page. Default report.html. "
                             "Put it where the video paths resolve from.")
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
    base = out.parent if str(out.parent) else Path(".")
    referenced = {m["source_path"] for m in record["matches"]}
    referenced |= {m["candidate_path"] for m in record["matches"]}
    missing = sorted(p for p in referenced if not (base / p).exists())

    out.write_text(render(record, missing))
    print(f"wrote {out}")
    if missing:
        print(f"{len(missing)} of {len(referenced)} videos do not resolve from "
              f"{base}/, so their players will be blank. Move the report, or "
              f"rerun find_reuse.py from the folder the paths are relative to.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
