"""What every command is for, in one place.

One catalogue rather than a docstring here and an embed description there. The
slash command descriptions Discord shows in the picker are one line each and
cannot say what a number means or what it refuses to do, which is most of what
is worth knowing about this tool. So the long version lives here and both the
overview and the detail view read from it.

Written to answer three questions per command in this order: what it tells you,
how to read it, and what it will not do. The third is the one that matters most
here, because nearly every command in this list has something it deliberately
declines to guess at.
"""

from __future__ import annotations

from dataclasses import dataclass

OVERVIEW = "all"


@dataclass(frozen=True)
class Topic:
    name: str
    group: str
    line: str                  # one line, for the list
    detail: str                # the longer read
    usage: str = ""

    @property
    def slash(self) -> str:
        return f"/{self.name}"


CATALOGUE: tuple[Topic, ...] = (
    Topic(
        "week", "Every week",
        "This week's lineup, with projections, opponents and PFF usage",
        "Your starters and bench for the week, each with ESPN's projection, "
        "the opponent, injury status and a PFF role line saying how he is "
        "actually being used. Floor, ceiling and boom odds come from what "
        "comparable players really did, not from a curve.\n\n"
        "It does not out-project ESPN. Every attempt to do that here was "
        "measured and lost, so the projection is ESPN's and everything around "
        "it describes the spread.",
        "/week rcl"),
    Topic(
        "startsit", "Every week",
        "Only the slots where there is a real question",
        "Silence is the point. A lineup that is already right produces "
        "nothing, so anything it does say is worth reading. The optimiser is "
        "an exact assignment over slot eligibility, which catches "
        "rearrangements a one-for-one swap check cannot, like moving a "
        "receiver into the flex so a back can take the RB slot.\n\n"
        "A gap has to clear a measured threshold before it is mentioned, "
        "because below that the projection does not predict which man "
        "outscores the other.",
        "/startsit dmwd"),
    Topic(
        "lookahead", "Every week",
        "Weeks coming up where the roster cannot fill a slot",
        "Byes and injuries stacking up ahead of you, week by week, so a hole "
        "in week 12 is something you find in week 9 rather than on the "
        "Sunday. It reports slots nothing on the roster can legally fill.",
        "/lookahead rcl"),
    Topic(
        "waivers", "The wire",
        "Free agents who would improve your lineup this week",
        "Priced on the whole lineup rather than player against player, so a "
        "man who frees a slot is worth more than his projection says. Shows "
        "what each add gains this week and what it costs in season value, in "
        "two columns, because they are different questions.\n\n"
        "It knows about pending claims, empty IR slots and players whose game "
        "has already kicked off.",
        "/waivers rcl"),
    Topic(
        "depth", "The wire",
        "Rostered players the wire beats at their own position",
        "A roster question rather than a lineup one. None of these change "
        "what you score on Sunday, which is exactly why the waiver view does "
        "not raise them: a bench player who will not play for a month is "
        "invisible there and obvious here.\n\n"
        "Points are compared only inside a position, because a quarterback's "
        "are not a receiver's.",
        "/depth dmwd"),
    Topic(
        "trades", "Trades",
        "One-for-ones that improve both rosters",
        "Ranked on the season axis, because the weekly one is close to zero "
        "sum: the points a trade moves into your lineup come out of theirs. "
        "Measured over 999 priced pairs, the two sides' weekly gains summed "
        "to a median of -0.6.\n\n"
        "ASK says how hard a sell each one is. Every deal comes with what it "
        "does to THEIR lineup, which is usually the number that looks wrong "
        "until you see the cascade.",
        "/trades rcl"),
    Topic(
        "target", "Trades",
        "Packages that would land one named player",
        "A ladder, cheapest ask first. Each rung down costs you a little more "
        "and is worth meaningfully more to him, so you open at the top and "
        "work down only as far as you have to.\n\n"
        "It does not require him to improve your starting lineup. A buy-low "
        "and a handcuff are both moves where he does not, so instead it says "
        "what the bet is: how far he would have to beat his own projection "
        "for the deal to pay. It prices the bet and never takes it.",
        "/target rcl player: Lamar Jackson"),
    Topic(
        "raid", "Trades",
        "What one manager's roster could give you",
        "Whether somebody trades at all is the biggest factor in whether a "
        "deal happens and the one thing none of these numbers can see. So "
        "pick the manager you know will talk and say how hard to push.\n\n"
        "Pushing surfaces longshots: deals the numbers say he loses, which a "
        "willing manager might take and a quiet one will ignore. One team is "
        "about eight seconds rather than the minute the league-wide search "
        "takes.",
        "/raid rcl team: Super Lamario"),
    Topic(
        "grade", "Trades",
        "Price an offer somebody actually sent you",
        "Any number of players a side, so counters and packages work. Most of "
        "the output is what changes in the lineup on both axes, because a "
        "total does not tell you where the points went.\n\n"
        "Uneven packages carry the roster size: it names who you would have "
        "to cut, chosen on the roster as it would be after the trade, and "
        "never offers an IR stash.",
        '/grade rcl give: "Davis, Dowdle" get: "Nabers"'),
    Topic(
        "compare", "Trades",
        "Any two players, and what swapping one for the other costs",
        "Whatever their positions, wherever they are: your roster, somebody "
        "else's, or nobody's. The swap is priced on the whole lineup both "
        "times.\n\n"
        "Underneath sits form week by week, the season projections "
        "differenced, the next five weeks of opponents with byes named, and a "
        "PFF table when the two share a position group. There is no "
        "week-by-week forecast because ESPN publishes none past the current "
        "week, and one is not invented.",
        "/compare rcl a: Gibbs b: Barkley"),
    Topic(
        "scoreboard", "Looking back",
        "Live scores across every league at once",
        "Ignores the league picker on purpose and shows all of them, which is "
        "what you want on a Sunday afternoon.",
        "/scoreboard"),
    Topic(
        "scorecard", "Looking back",
        "How this tool's own recommendations have actually done",
        "Every start/sit call and waiver suggestion is recorded when it is "
        "made and scored once the week finishes, including whether you acted "
        "on it. The honest version: it can tell you the advice was wrong as "
        "easily as it can tell you it was right.\n\n"
        "A row needs both sides to have played before it scores, so the week "
        "settles on Tuesday morning after the Monday night game.",
        "/scorecard"),
    Topic(
        "glossary", "Housekeeping",
        "What every Role and outcome number means",
        "Lives beside the code that renders those lines, so the explanation "
        "cannot drift from the output. Worth reading once.",
        "/glossary"),
    Topic(
        "health", "Housekeeping",
        "Per-league connection status",
        "Whether each league answers, and which credential is the problem "
        "when one does not. ESPN cookies die mid-season and this is how you "
        "find out it was that rather than something you changed.",
        "/health"),
    Topic(
        "clear", "Housekeeping",
        "Delete old messages in this channel",
        "Housekeeping for a channel nobody else reads. Discord refuses to "
        "bulk delete anything older than two weeks, so those go one at a "
        "time and it is slower.",
        "/clear limit: 50"),
    Topic(
        "help", "Housekeeping",
        "This list",
        "Every command, grouped, with the longer version behind the menu.",
        "/help"),
)

GROUPS: tuple[str, ...] = ("Every week", "The wire", "Trades", "Looking back",
                           "Housekeeping")


def find(name: str) -> Topic | None:
    wanted = (name or "").lstrip("/").lower()
    return next((t for t in CATALOGUE if t.name == wanted), None)


def in_group(group: str) -> list[Topic]:
    return [t for t in CATALOGUE if t.group == group]


def names() -> list[str]:
    return [t.name for t in CATALOGUE]
