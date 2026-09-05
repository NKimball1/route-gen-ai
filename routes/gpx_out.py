"""Write a RouteCandidate as a Garmin-importable GPX 1.1 track."""
from xml.sax.saxutils import escape

from routes.spec import RouteCandidate

GPX_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<gpx version="1.1" creator="cycling-agentic-flow route composer" '
    'xmlns="http://www.topografix.com/GPX/1/1">\n'
)


def write_track(points, name: str, desc: str, path: str) -> None:
    lines = [GPX_HEADER,
             f"  <trk><name>{escape(name)}</name><desc>{escape(desc)}</desc>\n"
             "    <trkseg>\n"]
    for lat, lon, ele in points:
        if ele is not None:
            lines.append(f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}">'
                         f"<ele>{ele:.1f}</ele></trkpt>\n")
        else:
            lines.append(f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}"/>\n')
    lines.append("    </trkseg>\n  </trk>\n</gpx>\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def write_gpx(candidate: RouteCandidate, name: str, path: str) -> None:
    # Stats in <desc> (the preview reads them); generation details go here
    # too so the GPX <name> stays clean in route viewers.
    desc = (f"{candidate.distance_mi:.1f} mi, {candidate.ascent_ft:.0f} ft "
            f"({candidate.shape}, {candidate.provider}, {candidate.seed})")
    write_track(candidate.points, name, desc, path)
