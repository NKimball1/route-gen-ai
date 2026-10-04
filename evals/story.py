"""The written half of the case study.

Prose lives here; NUMBERS never do. Every before/after quote on the page is
pulled out of the saved case records at build time by build_case_study.py, so
this file cannot claim an improvement the results do not show.
"""
from typing import Any

PROMPT_RULE = '''FIRST decide "route" vs "edit_route", using the LENGTH OF THE WHOLE RIDE:

- The request states a length or duration for the WHOLE ride ("30 miles",
  "a couple of hours", "45 mi loop") -> "route". It is still a "route" when
  it ALSO names roads to stay off or places to pass through; those belong in
  avoid_places / via_places. "I hate riding on X - give me a 22 mile loop
  from Y without it" is a ROUTE with avoid_places ["X"], NOT an avoid edit.
- The request states no length for the whole ride and only describes a
  CHANGE ("go past X", "route me through X and then Y", "avoid Z", "make it
  longer", "end at W") -> "edit_route". NEVER invent distance_miles to turn
  such a request into a "route": replacing the rider's current route with a
  new one they did not ask for is the worst mistake you can make here.'''

HEADLINE_FIX: dict[str, Any] = {
    "title": "A new ride request, answered as an edit to a route that did not exist",
    "trigger": "D15",
    "confirm": ["H23", "H24"],
    "file": "routes/nl.py",
    "steps": [
        ("The trigger",
         "Case D15 is an ordinary sentence: <em>&ldquo;I hate riding on Whitney "
         "Way &mdash; 22 mile loop from the Memorial Union Madison WI without "
         "it.&rdquo;</em> It asks for a new ride and gives a length, a start, "
         "and one road to stay off. The app came back with a refusal and no "
         "route at all."),
        ("What actually happened",
         "The parse returned <span class='mono'>request_type: “edit_route”"
         "</span>, mode <span class='mono'>avoid</span>, place <span class='mono'>"
         "Whitney Way</span> &mdash; and dropped <span class='mono'>"
         "distance_miles</span> entirely, because the edit branch of the schema "
         "has no such field. The service then looked for a current route to "
         "edit, found none, and said so. Every layer below the parse behaved "
         "correctly. The request was classified wrong one step earlier, and the "
         "22 miles the rider asked for never survived the first hop."),
        ("Root cause",
         "The system prompt introduced the <span class='mono'>avoid</span> edit "
         "mode with the example <em>&ldquo;I don't like riding Y&rdquo;</em> "
         "&mdash; almost exactly D15's opening clause. Nothing in the prompt "
         "said which signal decides between a new route and an edit, so a "
         "sentence carrying both a complaint and a complete route spec resolved "
         "toward whichever example it matched first."),
        ("The same bug in the other direction, and worse",
         "The held-out set contained the mirror image. H23, <em>&ldquo;route me "
         "through Vilas Park and then the Arboretum&rdquo;</em>, states no "
         "length, so it is plainly an edit &mdash; but it parsed as a new "
         "<span class='mono'>route</span> with an <strong>invented</strong> "
         "10-mile distance, and the app replaced the rider's 18-mile route with "
         "a 10-mile one they never asked for. The next request in that session, "
         "a bare <em>&ldquo;undo&rdquo;</em> (H24), then had nothing to step "
         "back to. One misread sentence destroyed the work and the way back to "
         "it."),
        ("The fix",
         "One rule at the top of the prompt, built on the only signal that "
         "actually separates the two cases: whether a length for the "
         "<em>whole ride</em> is stated. A stated length means a new route, "
         "even when the sentence also names roads to avoid or places to pass "
         "through. No stated length means an edit &mdash; with an explicit "
         "instruction never to invent a distance in order to treat it as a "
         "new route."),
        ("Did it work? Half of it did",
         "This is where a report that only reported wins would stop. "
         "<strong>D15 is fixed and stable</strong>: parsed five more times "
         "with the cache disabled, it came back as a route with the avoid "
         "constraint intact every time, and the app now returns the 22-mile "
         "loop it always should have. <strong>H23 is not fixed.</strong> "
         "Parsed five times, it classified correctly three times and "
         "reverted to inventing a distance twice &mdash; so on the campaign "
         "run it failed, and H24 cascaded behind it. One prompt rule moved "
         "a reliable failure to a coin flip in that direction. That is an "
         "improvement and it is not a fix, and the page reports it as "
         "both."),
    ],
    "code": PROMPT_RULE,
    "closing":
        "Two things this does NOT claim. First, the rule was written "
        "against D15, a development-set case, so D15 passing is not "
        "independent evidence &mdash; the held-out cases are, and they "
        "say the rule generalizes only partly. Second, nothing here was "
        "tuned on H23 or H24: their expectations were frozen before the "
        "rule existed and neither was used to shape it, which is exactly "
        "why they can report a partial result instead of a flattering "
        "one. The repository also grew six parse-regression cases "
        "covering both directions of the boundary, so a future prompt "
        "edit cannot quietly undo the half that does work."
        "<br><br>"
        "The remaining exposure is the interesting part. The parser "
        "cannot know whether a current route exists &mdash; that is "
        "session state the service holds, not something in the sentence. "
        "So the durable fix is not a better prompt at all: it is to make "
        "the mistake survivable, by recording a newly generated route's "
        "parent so undo can step back to the ride the rider had. That is "
        "designed and not built, deliberately: the only evidence for it "
        "in this campaign sits in the held-out set, and spending that "
        "evidence to chase one more green row would cost the one clean "
        "measurement this campaign has.",
}


OTHER_FIXES: list[dict[str, Any]] = [
    {
        "trigger": "D36",
        "file": "routes/service.py &middot; routes/pipeline.py &middot; routes/providers.py",
        "title": "A dead router told the rider to change their request",
        "what":
            "With the routing server unreachable, the progress log said ROUTING "
            "SERVER UNREACHABLE four times while the banner said <em>&ldquo;No "
            "route met the constraints &mdash; try a looser target or different "
            "distance.&rdquo;</em> The diagnosis existed; it never reached the "
            "person reading the screen, and it sent them to fix a request that "
            "was never the problem. The provider now records that the server "
            "never answered, and a one-second TCP preflight means a black-holed "
            "router costs a second rather than a dozen two-minute timeouts.",
    },
    {
        "trigger": "D37",
        "file": "routes/service.py",
        "title": "A geocoder failure escaped as a stack trace",
        "what":
            "An unreachable Nominatim raised <span class='mono'>requests."
            "ConnectionError</span> straight out of the service layer: a "
            "traceback on the CLI, and in the web app a banner reading "
            "<span class='mono'>ERROR: ConnectionError: HTTPConnectionPool"
            "(host=&hellip;)</span>. A place lookup that fails is an ordinary "
            "outcome of a request, so it is now a sentence &mdash; and the two "
            "failure modes get different advice, because a service being down "
            "is temporary while a place that does not exist needs the rider to "
            "rephrase. The held-out H14, <em>&ldquo;through the place with the "
            "good pie&rdquo;</em>, is the second failure mode and was not used "
            "to design the fix.",
    },
    {
        "trigger": "D18",
        "file": "routes/service.py",
        "title": "An interval stretch too short for the rep, presented without the caveat",
        "what":
            "A 2&times;20 threshold rep needs about 6.7 mi of road at the app's "
            "assumed pace. The search returned a 5.5 mi stretch &mdash; a "
            "legitimate answer, since lapping a short stretch is supported "
            "&mdash; and labelled it with no hint that it takes two laps and a "
            "turnaround per rep. The CLI had computed that lap count all along "
            "and printed it; the label the web user reads dropped it.",
    },
]

THEME = (
    "Three of the four were the same shape, which is the part worth noticing. "
    "None of them was a wrong computation. In each case the program had already "
    "worked out the truth &mdash; that the router was down, that the lookup "
    "failed, that the stretch needs two laps &mdash; and then handed the user a "
    "sentence that did not contain it. A correct internal state and a misleading "
    "message is indistinguishable from a bug to the person using the thing, and "
    "no unit test asserting on return values would have caught any of them. "
    "They were found by writing down what the user should see and then checking "
    "the user-visible string against it."
)

SCORER_CALIBRATION = [
    ("A 600 m gap threshold flagged three good routes.",
     "Wisconsin's section-line grid gives dead-straight half-mile and mile-long "
     "roads, which the router emits as a single segment. Raw gap length is not "
     "evidence of a break."),
    ("Requiring a sharp turn at one end still flagged grid corners.",
     "You turn 90&deg; onto a section road and then run straight for half a "
     "mile: entered with a turn, left without one. The check now requires the "
     "long segment to be off-axis at <em>both</em> ends, which a corner never "
     "is and a splice always is."),
    ("The out-and-back detector scored an exact out-and-back at 0.6%.",
     "It resampled at a fixed step and compared by index, so the two halves "
     "drifted out of phase. Rewritten to compare positions by arc length. "
     "Until this was fixed the scorer was calling every out-and-back an "
     "accidental doubling-back."),
]
