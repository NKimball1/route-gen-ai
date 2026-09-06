"""Natural-language request parsing: plain English -> a typed request dict.

This is the ONLY place an LLM is used. It translates intent; it never touches
geometry. Cost design (for eventually serving this from a website): one small
call per request on a cheap model — claude-haiku-4-5 by default (~600 input +
~200 output tokens ≈ $0.002/request; override with the ROUTEGEN_MODEL env
var). Structured outputs guarantee schema-valid JSON, so there is no retry
loop to pay for.
"""
import json
import os

import anthropic

DEFAULT_MODEL = "claude-haiku-4-5"

SYSTEM = """You convert a cyclist's plain-English request into JSON for a \
route-generation tool. Three request types:

- "edit_route": the user wants to MODIFY the previous/current/uploaded
  route while keeping most of it. Modes:
  - "avoid": route AROUND a place/road ("find a way around X", "not
    through Z", "I don't like riding Y"). radius_m sizes the area
    (default 1000; a large park ~1500, one intersection ~300).
  - "via": route THROUGH/ALONG something ("go down the commuter path
    instead", "add a detour to hit Cafe X", "swing by Y"). radius_m
    ~1500. "instead" about a road the user WANTS = via, never avoid.
  - "extend" / "shorten": change the length ("make it longer/shorter").
    Put the CHANGE in miles in miles_delta ("about 10 more miles" ->
    extend, miles_delta 10). If the user gives a TOTAL instead ("make it
    40 miles total"), put the total in target_miles and leave miles_delta
    null — the tool knows the current length.
  - "move_start" / "move_end": start OR finish somewhere else ("end at
    X", "start from Y instead"). place = the new point.
  - "anchor": start AND end at the same place — a round trip from there
    ("start and end at X", "make it start and finish at my house",
    "round trip from X"). Never split this into move_start alone.
  - "connect": add a leg FROM an address TO the route's start ("route me
    from ADDR to the start of this ride"). connect_return=true when they
    also want the leg home at the end ("...and back to ADDR after").
  Put places in edit.place as geocodable strings (append city/state).

- "route": a ride of a target distance (a loop or out-and-back from a start
  address). Map "out and back or loop is fine" to shape "both". Map "as much
  climbing as you can" / "maximize elevation" to maximize_climb=true, and
  "as flat as possible" / "least elevation" to minimize_climb=true. Soft
  climbing language: "not much climbing" ≈ max_climb_ft 1000 for rides up to
  35 mi, scale proportionally for longer. If the user gives a duration
  instead of distance, assume 16 mph average. Roads/areas the user wants to
  avoid go in avoid_places as geocodable place strings (append the city if
  the user's address makes it obvious). Places the route should pass
  THROUGH ("goes through X and Y") go in via_places, in the user's order,
  as geocodable strings (append the state).
- "interval_spot": the user wants a STRETCH OF ROAD to do structured
  intervals on, not a full route. Threshold / tempo / sweet-spot / TT work
  wants kind "flat"; VO2 / hill reps / "ride against an incline" wants
  "incline". The tool already minimizes traffic interruptions (stop signs,
  signals) and prefers steady grades — don't put those in notes. "2x20" means reps=2, rep_minutes=20. Travel budget: use the
  user's stated limit ("within 30 minutes"), else default 30. "Close to my
  house" ≈ 15.

Set address to the start address as given. If the user says "home" / "my
house" or gives no address, set address to null (the tool knows the home
address).
Exactly one of "route"/"interval"/"edit" is non-null, matching request_type.
Anything you could not represent goes in notes (else empty string)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "request_type": {"type": "string",
                         "enum": ["route", "interval_spot", "edit_route"]},
        "address": {"type": ["string", "null"]},
        "route": {
            "type": ["object", "null"],
            "properties": {
                "distance_miles": {"type": "number"},
                "max_climb_ft": {"type": ["number", "null"]},
                "maximize_climb": {"type": "boolean"},
                "minimize_climb": {"type": "boolean"},
                "shape": {"type": "string", "enum": ["loop", "outback", "both"]},
                "avoid_places": {"type": "array", "items": {"type": "string"}},
                "via_places": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["distance_miles", "max_climb_ft", "maximize_climb",
                         "minimize_climb", "shape", "avoid_places", "via_places"],
            "additionalProperties": False,
        },
        "interval": {
            "type": ["object", "null"],
            "properties": {
                "reps": {"type": "integer"},
                "rep_minutes": {"type": "number"},
                "kind": {"type": "string", "enum": ["flat", "incline"]},
                "max_travel_minutes": {"type": "number"},
            },
            "required": ["reps", "rep_minutes", "kind", "max_travel_minutes"],
            "additionalProperties": False,
        },
        "edit": {
            "type": ["object", "null"],
            "properties": {
                "mode": {"type": "string",
                         "enum": ["avoid", "via", "extend", "shorten",
                                  "move_start", "move_end", "anchor",
                                  "connect"]},
                "place": {"type": ["string", "null"]},
                "radius_m": {"type": "number"},
                "miles_delta": {"type": ["number", "null"]},
                "target_miles": {"type": ["number", "null"]},
                "connect_return": {"type": "boolean"},
            },
            "required": ["mode", "place", "radius_m", "miles_delta",
                         "target_miles", "connect_return"],
            "additionalProperties": False,
        },
        "notes": {"type": "string"},
    },
    "required": ["request_type", "address", "route", "interval", "edit",
                 "notes"],
    "additionalProperties": False,
}


def parse_request(text: str, client: anthropic.Anthropic | None = None) -> dict:
    """Parse a plain-English request. Returns the schema dict plus _usage."""
    client = client or anthropic.Anthropic()
    model = os.environ.get("ROUTEGEN_MODEL", DEFAULT_MODEL)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": text}],
    )
    raw = next(b.text for b in response.content if b.type == "text")
    parsed = json.loads(raw)
    parsed["_usage"] = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return parsed
