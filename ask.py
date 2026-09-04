"""Plain-English entry point: describe the ride, get GPX.

  python ask.py "give me a 30ish mile loop from 123 Main St, Madison WI, under 1000 ft of climbing"
  python ask.py "find me a flat spot within 30 min of home for 2x20 threshold intervals"
  python ask.py "50 miles, as much climbing as you can, out and back or loop is fine"

Needs ANTHROPIC_API_KEY (env or .env). Set ROUTEGEN_HOME_ADDRESS to make
"from home" / address-less requests work. Routing itself uses no LLM.
"""
import os
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    text = " ".join(sys.argv[1:])

    from routes.nl import parse_request
    req = parse_request(text)
    usage = req.pop("_usage")
    print(f"Parsed ({usage['model']}, {usage['input_tokens']}in/"
          f"{usage['output_tokens']}out tokens): {req}")

    address = req.get("address") or os.environ.get("ROUTEGEN_HOME_ADDRESS")
    if not address:
        print("No start address in the request and ROUTEGEN_HOME_ADDRESS is "
              "not set — add the address to your request.")
        return 1
    if req.get("notes"):
        print(f"Note: couldn't map: {req['notes']}")

    if req["request_type"] == "interval_spot":
        from find_spot import run_spot_search
        from routes.intervals import IntervalSpec
        iv = req["interval"]
        spec = IntervalSpec(address, iv["reps"], iv["rep_minutes"], iv["kind"],
                            iv["max_travel_minutes"])
        return 0 if run_spot_search(spec) else 1

    from compose_route import parse_avoid
    from routes.pipeline import build_providers, compose
    from routes.spec import RouteSpec
    r = req["route"]
    avoid = parse_avoid(r["avoid_places"])
    shapes = ["loop", "outback"] if r["shape"] == "both" else [r["shape"]]
    specs = [RouteSpec.from_imperial(address, r["distance_miles"], r["max_climb_ft"],
                                     r["maximize_climb"], shape=s, avoid=avoid,
                                     minimize_climb=r["minimize_climb"])
             for s in shapes]
    keepers = compose(specs, build_providers())
    return 0 if keepers else 1


if __name__ == "__main__":
    sys.exit(main())
