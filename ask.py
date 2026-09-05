"""Plain-English entry point: describe the ride, get GPX.

  python ask.py "give me a 30ish mile loop from 123 Main St, Madison WI, under 1000 ft of climbing"
  python ask.py "find me a flat spot within 30 min of home for 2x20 threshold intervals"
  python ask.py "detour around Pheasant Branch Conservancy, use roads"

Needs ANTHROPIC_API_KEY (env or .env). Set ROUTEGEN_HOME_ADDRESS to make
"from home" / address-less requests work. Routing itself uses no LLM.
The web UI (api.py) is the same brain with a map.
"""
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    text = " ".join(sys.argv[1:])

    from routes.service import handle_request
    result = handle_request(text, log_sink=sys.stdout)
    return 0 if result["candidates"] else 1


if __name__ == "__main__":
    sys.exit(main())
