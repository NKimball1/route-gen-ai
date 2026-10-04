"""Edit an existing route: detour around a place, keep the rest.

  python edit_route.py --avoid "Pheasant Branch Conservancy, Middleton WI:1200"
  python edit_route.py --via "Colectivo Coffee, Monroe St Madison"
  python edit_route.py --route path\\to\\some.gpx --avoid "..."

Without --route, edits the current route (the last composed or edited
winner, tracked in output/routes/latest.txt). The edited route becomes the
new current route, so edits chain. Radius defaults to 1000 m; append
":<meters>" to the place to widen/narrow the zone.
"""
import argparse
import sys
from dotenv import load_dotenv

# before the route imports: some modules read settings (e.g. ROUTEGEN_TOTAL_KG)
# at import time
load_dotenv()

from routes.edit_service import run_edit as run_edit, normalize_places as normalize_places
from routes.storage import (current_route as current_route, predecessor as predecessor,
                            record_parent as record_parent, note_outcome as note_outcome,
                            last_edit_changed as last_edit_changed)

__all__ = ['run_edit', 'normalize_places', 'current_route', 'predecessor', 'record_parent', 'note_outcome', 'last_edit_changed']

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--route", default=None,
                    help="GPX to edit (default: the current route)")
    ap.add_argument("--avoid", default=None,
                    help='place to route around, "name" or "name:radius_m"')
    ap.add_argument("--via", default=None,
                    help="place to route through instead")
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    if bool(args.avoid) == bool(args.via):
        print("Pass exactly one of --avoid or --via.")
        return 1

    route_path = args.route or current_route()
    if route_path is None:
        print("No current route — compose one first, or pass --route.")
        return 1
    print(f"Editing: {route_path}")

    raw = args.avoid or args.via
    place, _, radius = raw.rpartition(":")
    if place and radius.replace(".", "").isdigit():
        radius_m = float(radius)
    else:
        place, radius_m = raw, 1000.0

    mode = "via" if args.via else "avoid"
    out, message, _ok = run_edit(route_path, place, radius_m, mode,
                                 args.profile)
    print(message)
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
