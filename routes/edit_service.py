"""Application-level route editing; CLI and web share this implementation."""
import os
from routes.editing import (anchor_at, connect_from, detour_around, extend_route,
                            move_endpoint, route_via, route_via_chain, shorten_route)
from routes.geocode import geocode_flexible
from routes.gpx_out import write_track
from routes.preview import _parse_gpx, build_preview
from routes.providers import BRouterProvider
from routes.spec import METERS_PER_FOOT, METERS_PER_MILE, Outcome
from routes.storage import artifact_path, record_parent, select_route, transaction, note_outcome

OUT_DIR: str = os.path.join("output", "routes")
# Via edits need room to leave and rejoin the route around the target.
VIA_BUFFER_MIN_M: float = 1200.0
# Place lookups are biased to the route's neighborhood: bbox padded by
# these degrees (~15 km at Midwest latitudes).
NEAR_MARGIN_LAT_DEG: float = 0.15
NEAR_MARGIN_LON_DEG: float = 0.2


def normalize_places(place: str | None, places: list[str] | None
                     ) -> tuple[str | None, list[str] | None]:
    """The parser may put a LONE place in the `places` list ('go through
    Olbrich Park' -> places=[...], place=None). One place is one place,
    whichever field it arrived in."""
    places = [p for p in (places or []) if p and p.strip()]
    if not place and len(places) == 1:
        return places[0], None
    return place, (places or None)


def run_edit(route_path: str, place: str | None = None,
             radius_m: float = 1000.0, mode: str = "avoid",
             profile: str | None = None, out_dir: str = OUT_DIR,
             miles_delta: float | None = None,
             connect_return: bool = False,
             places: list[str] | None = None
             ) -> tuple[str | None, str, Outcome]:
    with transaction(out_dir):
        return _run_edit(route_path, place, radius_m, mode, profile, out_dir,
                         miles_delta, connect_return, places)


def _run_edit(route_path: str, place: str | None = None,
             radius_m: float = 1000.0, mode: str = "avoid",
             profile: str | None = None, out_dir: str = OUT_DIR,
             miles_delta: float | None = None,
             connect_return: bool = False,
             places: list[str] | None = None
             ) -> tuple[str | None, str, Outcome]:
    """Edit route_path. Modes: avoid, via, extend, shorten, move_start,
    move_end, anchor, connect. Returns (new GPX path or None, a
    human-readable outcome message, ok: True | "partial" | False).
    Avoid edits VERIFY the outcome: the final route is measured against
    the zone rather than trusting that every section rerouted."""
    place, places = normalize_places(place, places)
    points = _parse_gpx(route_path)
    if not points:
        return None, f"Couldn't read the route file ({route_path}).", False
    provider = BRouterProvider(profile=profile)

    # bias place lookups to the route's own neighborhood (~15 km margin)
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    near = (min(lats) - NEAR_MARGIN_LAT_DEG,
            min(lons) - NEAR_MARGIN_LON_DEG,
            max(lats) + NEAR_MARGIN_LAT_DEG,
            max(lons) + NEAR_MARGIN_LON_DEG)

    skipped: list[str] = []
    if mode in ("extend", "shorten"):
        if not miles_delta or miles_delta <= 0:
            return None, "How much longer/shorter? Give a number of miles.", False
        meters = miles_delta * METERS_PER_MILE
        if mode == "extend":
            print(f"Extending by ~{miles_delta:.0f} mi")
            result = extend_route(points, meters, provider)
        else:
            print(f"Shortening by ~{miles_delta:.0f} mi")
            result = shorten_route(points, meters, provider)
        if result is None:
            return None, (f"Couldn't find a good way to {mode} this route "
                          "by that much — it's unchanged."), False
        place = f"{mode} {miles_delta:.0f}mi"
    elif mode == "via" and places and len(places) > 1:
        targets, names = [], []
        for p in places:
            try:
                zlat, zlon, zname = geocode_flexible(p, near=near)
                print(f"Waypoint: {zname}")
                targets.append((zlat, zlon))
                names.append(p)
            except ValueError:
                skipped.append(p)
        if not targets:
            return None, ("Couldn't locate any of those places near the "
                          f"route ({', '.join(places)}) — try road names "
                          "plus the city, or landmarks."), False
        result = route_via_chain(points, targets, provider,
                                 buffer_m=max(radius_m, VIA_BUFFER_MIN_M))
        if result is None:
            return None, ("Couldn't route through those places — the "
                          "route is unchanged."), False
        place = " + ".join(names)
    else:
        if not place:
            return None, "That edit needs a place or address.", False
        try:
            zlat, zlon, zname = geocode_flexible(place, near=near)
        except ValueError as e:
            return None, (f"Couldn't find {place!r} near your route "
                          f"({e}). The route is unchanged."), False
        if mode == "via":
            print(f"Routing through: {zname}")
            result = route_via(points, (zlat, zlon),
                               provider, buffer_m=max(radius_m, VIA_BUFFER_MIN_M))
            if result is None:
                return None, (f"Couldn't splice the route through "
                              f"{place!r} — it's unchanged."), False
        elif mode in ("move_start", "move_end"):
            where = "start" if mode == "move_start" else "end"
            print(f"Moving the {where} to: {zname}")
            result = move_endpoint(points, (zlat, zlon), provider, at=where)
            if result is None:
                return None, (f"Couldn't move the {where} there — the "
                              "route is unchanged."), False
        elif mode == "anchor":
            print(f"Making it a round trip from: {zname}")
            result = anchor_at(points, (zlat, zlon), provider)
            if result is None:
                return None, ("The ride already starts and ends there — "
                              "nothing to change."), False
        elif mode == "connect":
            print(f"Connecting from: {zname}"
                  + (" (and back at the end)" if connect_return else ""))
            result = connect_from(points, (zlat, zlon), provider,
                                  with_return=connect_return)
            if result is None:
                return None, ("Couldn't route from there to the ride — "
                              "the route is unchanged."), False
        else:
            # a named ROAD is a line — try its real OSM geometry first, so
            # 'avoid Whitney Way' guards the road, not one point on it
            from routes.road_avoid import (detour_around_road, fetch_road,
                                           looks_like_road, on_road_meters)
            # only road-like names get road mode: 'Pheasant Branch
            # Conservancy' must not match 'Pheasant Branch Road' by regex
            road_ways = (fetch_road(place.split(",")[0], zlat, zlon, 12000)
                         if looks_like_road(place) else [])
            road_result = None
            if road_ways:
                before_m = on_road_meters(points, road_ways)
                if before_m >= 60.0:
                    print(f"Avoiding the road itself: {zname} "
                          f"(riding {before_m / METERS_PER_MILE:.1f} mi along it)")
                    road_result = detour_around_road(points, road_ways,
                                                     provider)
            if road_result is not None:
                result = road_result
                result.road_mode = True
                after_m = on_road_meters(result.points, road_ways)
                result.fail_reason = result.fail_reason or ""
                if after_m <= 30.0:
                    print("  verified: no longer rides along it")
                else:
                    result.failed_detours = max(result.failed_detours, 1)
                    result.fail_reason = (f"still rides "
                                          f"{after_m / METERS_PER_MILE:.1f} mi of it")
            else:
                print(f"Detouring around: {zname} (r={radius_m:.0f} m)")
                result = detour_around(points, (zlat, zlon, radius_m),
                                       provider)
            if (result is not None and result.detours == 0
                    and result.failed_detours > 0):
                return None, (f"Couldn't avoid {place!r}: "
                              f"{result.fail_reason}. The route is "
                              "unchanged."), False
            if result is None:
                return None, (f"The route never passes through {place!r} — "
                              "nothing to change."), False

    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(route_path).rsplit(".", 1)[0]
    base = base.split("_edit")[0]
    out_path = artifact_path(out_dir, f"{base}_edit")

    verbs = {"via": "via", "avoid": "around", "extend": "",
             "shorten": "", "move_start": "start at",
             "move_end": "end at", "connect": "connect",
             "anchor": "round trip from"}
    desc = (f"{result.distance_m / METERS_PER_MILE:.1f} mi, "
            f"{result.ascent_m / METERS_PER_FOOT:.0f} ft "
            f"(edit: {verbs.get(mode, mode)} {place})".replace(":  ", ": "))
    write_track(result.points, f"{base} edit", desc, out_path)
    record_parent(out_path, route_path)
    build_preview([out_path, route_path], os.path.splitext(out_path)[0] + ".html")
    select_route(out_path, out_dir)
    note_outcome(out_dir, True)

    delta = result.added_m - result.removed_m
    print(f"Replaced {result.removed_m / METERS_PER_MILE:.1f} mi with "
          f"{result.added_m / METERS_PER_MILE:.1f} mi across "
          f"{result.detours} detour(s) ({delta / METERS_PER_MILE:+.1f} mi)")
    print(f"Edited route: {result.distance_m / METERS_PER_MILE:.1f} mi, "
          f"{result.ascent_m / METERS_PER_FOOT:.0f} ft, "
          f"{result.overlap_frac:.0%} repeat -> {out_path}")
    message = (f"Done — {verbs.get(mode, mode)} {place}: now "
               f"{result.distance_m / METERS_PER_MILE:.1f} mi "
               f"({delta / METERS_PER_MILE:+.1f} mi).")
    ok: Outcome = True
    if skipped:
        message += f" Couldn't locate and skipped: {', '.join(skipped)}."
        ok = "partial"
    if result.failed_detours:
        message = (f"Partly done — rerouted {result.detours} section(s) "
                   f"around {place}, but {result.failed_detours} couldn't "
                   f"be ({result.fail_reason}). "
                   f"Now {result.distance_m / METERS_PER_MILE:.1f} mi.")
        ok = "partial"
    if mode == "avoid" and result.road_mode:
        # road mode verified itself by measuring on-road meters
        if ok is True:
            message += " Verified: no longer rides along it."
    elif mode == "avoid":
        # verify the OUTCOME: does the final route actually clear the zone?
        from routes.editing import _dist_m
        min_d = min(_dist_m(p, (zlat, zlon)) for p in result.points)
        if min_d >= radius_m:
            if ok is True:
                message += " Verified clear of the area."
        elif ok is True:
            message = (f"Partly done — the route was rerouted but still "
                       f"passes within {min_d:.0f} m of {place}. "
                       f"Now {result.distance_m / METERS_PER_MILE:.1f} mi.")
            ok = "partial"
    if result.warnings:
        message = "Partly done — " + message.removeprefix("Done — ")
        message += " " + " ".join(result.warnings)
        ok = "partial"
    return out_path, message, ok

