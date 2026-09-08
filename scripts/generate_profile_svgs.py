import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import requests


# ============================================================
# Configuration
# ============================================================

USERNAME = os.environ["GITHUB_USERNAME"]
TOKEN = os.environ["GITHUB_TOKEN"]

OUTPUT_DIR = Path("profile")

# Tokyo Night-inspired colors
BG = "#1a1b27"
BORDER = "#2f3441"

TITLE = "#70a5fd"
TEXT = "#c0caf5"
MUTED = "#9aa5ce"

ACCENT = "#bb9af7"
ZERO_BAR = "#414868"

FONT = "Segoe UI, Ubuntu, Sans-Serif"


# ============================================================
# GitHub HTTP session
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{USERNAME}-profile-stats-generator",
    }
)


# ============================================================
# GitHub API helpers
# ============================================================

def github_rest(path, params=None):
    url = f"https://api.github.com{path}"

    response = session.get(
        url,
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def github_graphql(query, variables=None):
    response = session.post(
        "https://api.github.com/graphql",
        json={
            "query": query,
            "variables": variables or {},
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if "errors" in data:
        raise RuntimeError(
            f"GitHub GraphQL returned errors: {data['errors']}"
        )

    return data["data"]


# ============================================================
# GitHub profile data
# ============================================================

def get_user_data():
    return github_rest(f"/users/{USERNAME}")


def get_all_public_repos():
    """
    Fetch all public repositories owned by the user.

    Forks are excluded because stars on forked repositories should not
    normally be counted as stars earned by the user's own repositories.
    """

    repos = []
    page = 1

    while True:
        result = github_rest(
            f"/users/{USERNAME}/repos",
            params={
                "per_page": 100,
                "page": page,
                "type": "owner",
                "sort": "updated",
            },
        )

        if not result:
            break

        repos.extend(result)

        if len(result) < 100:
            break

        page += 1

    return [
        repo
        for repo in repos
        if not repo.get("fork", False)
    ]


# ============================================================
# Contribution data
# ============================================================

def get_contribution_years():
    """
    Ask GitHub which years contain contribution data.

    We additionally include the current year so the current streak
    remains available even when the year has very little activity.
    """

    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionYears
        }
      }
    }
    """

    data = github_graphql(
        query,
        {
            "login": USERNAME,
        },
    )

    years = data["user"]["contributionsCollection"]["contributionYears"]

    current_year = datetime.now(timezone.utc).year

    years = set(years)
    years.add(current_year)

    return sorted(years)


def get_contributions_for_year(year):
    """
    Fetch one calendar year only.

    GitHub rejects contributionCollection queries whose from/to span
    exceeds approximately one year, so every year is queried separately.
    """

    now = datetime.now(timezone.utc)

    start = datetime(
        year,
        1,
        1,
        0,
        0,
        0,
        tzinfo=timezone.utc,
    )

    end = datetime(
        year,
        12,
        31,
        23,
        59,
        59,
        tzinfo=timezone.utc,
    )

    if year == now.year:
        end = now

    query = """
    query(
      $login: String!,
      $from: DateTime!,
      $to: DateTime!
    ) {
      user(login: $login) {
        contributionsCollection(
          from: $from,
          to: $to
        ) {
          contributionCalendar {
            totalContributions

            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }

          totalCommitContributions
          totalIssueContributions
          totalPullRequestContributions
          totalPullRequestReviewContributions
        }
      }
    }
    """

    data = github_graphql(
        query,
        {
            "login": USERNAME,
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": end.isoformat().replace("+00:00", "Z"),
        },
    )

    return data["user"]["contributionsCollection"]


def get_all_contributions():
    """
    Fetch each contribution year independently, then merge everything
    locally.

    This avoids GitHub's one-year contributionCollection limit while
    still allowing longest-streak calculations across multiple years.
    """

    years = get_contribution_years()

    print(
        "Contribution years:",
        ", ".join(str(year) for year in years),
    )

    contributions = []

    for year in years:
        print(f"Fetching contribution data for {year}...")

        contribution = get_contributions_for_year(year)

        contributions.append(
            {
                "year": year,
                "data": contribution,
            }
        )

    return contributions


# ============================================================
# Contribution processing
# ============================================================

def build_contribution_map(yearly_contributions):
    """
    Convert GitHub's week/day structure into:

        {
            date(...): contribution_count,
            ...
        }

    Duplicated boundary dates are safely overwritten with the latest
    value returned by GitHub.
    """

    result = {}

    for yearly in yearly_contributions:
        contribution = yearly["data"]

        calendar = contribution["contributionCalendar"]

        for week in calendar["weeks"]:
            for day in week["contributionDays"]:
                day_date = date.fromisoformat(day["date"])

                result[day_date] = day["contributionCount"]

    return result


def calculate_current_streak(contribution_map):
    """
    Current streak follows the usual GitHub streak behavior:

    - If today has contributions, start from today.
    - Otherwise, yesterday may still be the end of the current streak.
    - If neither today nor yesterday has contributions, current streak
      is zero.
    """

    today = datetime.now(timezone.utc).date()

    if contribution_map.get(today, 0) > 0:
        cursor = today

    elif contribution_map.get(today - timedelta(days=1), 0) > 0:
        cursor = today - timedelta(days=1)

    else:
        return 0

    streak = 0

    while contribution_map.get(cursor, 0) > 0:
        streak += 1
        cursor -= timedelta(days=1)

    return streak


def calculate_longest_streak(contribution_map):
    """
    Calculate the longest sequence of consecutive active dates.

    Date adjacency is explicitly checked so gaps between queried years
    can never accidentally count as part of the same streak.
    """

    active_dates = sorted(
        day
        for day, count in contribution_map.items()
        if count > 0
    )

    if not active_dates:
        return 0

    longest = 1
    current = 1

    previous = active_dates[0]

    for current_date in active_dates[1:]:

        if current_date == previous + timedelta(days=1):
            current += 1

        else:
            current = 1

        longest = max(longest, current)

        previous = current_date

    return longest


def get_last_active_date(contribution_map):
    active_dates = [
        day
        for day, count in contribution_map.items()
        if count > 0
    ]

    if not active_dates:
        return None

    return max(active_dates)


def get_recent_days(contribution_map, number_of_days=30):
    today = datetime.now(timezone.utc).date()

    start = today - timedelta(days=number_of_days - 1)

    result = []

    for offset in range(number_of_days):
        current_date = start + timedelta(days=offset)

        result.append(
            {
                "date": current_date,
                "contributionCount": contribution_map.get(
                    current_date,
                    0,
                ),
            }
        )

    return result


def aggregate_contribution_stats(yearly_contributions):
    totals = {
        "contributions": 0,
        "commits": 0,
        "issues": 0,
        "pull_requests": 0,
        "reviews": 0,
    }

    for yearly in yearly_contributions:
        contribution = yearly["data"]

        totals["contributions"] += (
            contribution["contributionCalendar"]["totalContributions"]
        )

        totals["commits"] += contribution[
            "totalCommitContributions"
        ]

        totals["issues"] += contribution[
            "totalIssueContributions"
        ]

        totals["pull_requests"] += contribution[
            "totalPullRequestContributions"
        ]

        totals["reviews"] += contribution[
            "totalPullRequestReviewContributions"
        ]

    return totals


# ============================================================
# Formatting helpers
# ============================================================

def fmt_number(value):
    return f"{value:,}"


def format_pretty_date(value):
    if value is None:
        return "No activity"

    return value.strftime("%d %b %Y")


# ============================================================
# SVG helpers
# ============================================================

def build_svg_card(
    title,
    width,
    height,
    content,
):
    return f"""<svg xmlns="http://www.w3.org/2000/svg"
     width="{width}"
     height="{height}"
     viewBox="0 0 {width} {height}"
     role="img"
     aria-label="{escape(title)}">

  <rect
    x="1"
    y="1"
    width="{width - 2}"
    height="{height - 2}"
    rx="14"
    fill="{BG}"
    stroke="{BORDER}"
    stroke-width="1.5"
  />

  <text
    x="24"
    y="36"
    fill="{TITLE}"
    font-size="20"
    font-family="{FONT}"
    font-weight="700"
  >
    {escape(title)}
  </text>

  {content}
</svg>
"""


def stat_item(x, y, label, value):
    return f"""
  <text
    x="{x}"
    y="{y}"
    fill="{MUTED}"
    font-size="12"
    font-family="{FONT}"
  >
    {escape(label)}
  </text>

  <text
    x="{x}"
    y="{y + 24}"
    fill="{TEXT}"
    font-size="24"
    font-family="{FONT}"
    font-weight="700"
  >
    {escape(value)}
  </text>
"""


# ============================================================
# Stats SVG
# ============================================================

def generate_stats_svg(
    user_data,
    repos,
    contribution_totals,
):
    total_stars = sum(
        repo.get("stargazers_count", 0)
        for repo in repos
    )

    public_repos = user_data.get(
        "public_repos",
        0,
    )

    followers = user_data.get(
        "followers",
        0,
    )

    items = [
        (
            "Public Repositories",
            fmt_number(public_repos),
        ),
        (
            "Total Stars",
            fmt_number(total_stars),
        ),
        (
            "Followers",
            fmt_number(followers),
        ),
        (
            "Total Contributions",
            fmt_number(
                contribution_totals["contributions"]
            ),
        ),
        (
            "Commits",
            fmt_number(
                contribution_totals["commits"]
            ),
        ),
        (
            "Pull Requests",
            fmt_number(
                contribution_totals["pull_requests"]
            ),
        ),
    ]

    positions = [
        (28, 68),
        (280, 68),

        (28, 118),
        (280, 118),

        (28, 168),
        (280, 168),
    ]

    content = []

    for (label, value), (x, y) in zip(
        items,
        positions,
    ):
        content.append(
            stat_item(
                x,
                y,
                label,
                value,
            )
        )

    return build_svg_card(
        "GitHub Stats",
        520,
        220,
        "".join(content),
    )


# ============================================================
# Streak SVG
# ============================================================

def generate_streak_svg(
    contribution_map,
    total_contributions,
):
    current_streak = calculate_current_streak(
        contribution_map
    )

    longest_streak = calculate_longest_streak(
        contribution_map
    )

    last_active = get_last_active_date(
        contribution_map
    )

    recent_days = get_recent_days(
        contribution_map,
        30,
    )

    counts = [
        day["contributionCount"]
        for day in recent_days
    ]

    max_count = max(counts) if counts else 1

    if max_count == 0:
        max_count = 1

    chart_left = 28
    chart_bottom = 195

    bar_width = 10
    bar_gap = 4

    max_bar_height = 42

    bars = []

    for index, day in enumerate(recent_days):

        count = day["contributionCount"]

        if count == 0:
            bar_height = 4
            bar_color = ZERO_BAR

        else:
            bar_height = max(
                6,
                round(
                    (count / max_count)
                    * max_bar_height
                ),
            )

            bar_color = ACCENT

        x = chart_left + index * (
            bar_width + bar_gap
        )

        y = chart_bottom - bar_height

        bars.append(
            f"""
  <rect
    x="{x}"
    y="{y}"
    width="{bar_width}"
    height="{bar_height}"
    rx="3"
    fill="{bar_color}"
  />
"""
        )

    if recent_days:
        start_label = format_pretty_date(
            recent_days[0]["date"]
        )

        end_label = format_pretty_date(
            recent_days[-1]["date"]
        )

    else:
        start_label = ""
        end_label = ""

    content = f"""
  <text
    x="28"
    y="68"
    fill="{MUTED}"
    font-size="12"
    font-family="{FONT}"
  >
    Current Streak
  </text>

  <text
    x="28"
    y="94"
    fill="{TEXT}"
    font-size="25"
    font-family="{FONT}"
    font-weight="700"
  >
    {current_streak} days
  </text>


  <text
    x="280"
    y="68"
    fill="{MUTED}"
    font-size="12"
    font-family="{FONT}"
  >
    Longest Streak
  </text>

  <text
    x="280"
    y="94"
    fill="{TEXT}"
    font-size="25"
    font-family="{FONT}"
    font-weight="700"
  >
    {longest_streak} days
  </text>


  <text
    x="28"
    y="126"
    fill="{MUTED}"
    font-size="12"
    font-family="{FONT}"
  >
    Last Active
  </text>

  <text
    x="28"
    y="149"
    fill="{TEXT}"
    font-size="17"
    font-family="{FONT}"
    font-weight="700"
  >
    {escape(format_pretty_date(last_active))}
  </text>


  <text
    x="280"
    y="126"
    fill="{MUTED}"
    font-size="12"
    font-family="{FONT}"
  >
    Total Contributions
  </text>

  <text
    x="280"
    y="149"
    fill="{TEXT}"
    font-size="17"
    font-family="{FONT}"
    font-weight="700"
  >
    {fmt_number(total_contributions)}
  </text>


  <text
    x="28"
    y="171"
    fill="{MUTED}"
    font-size="11"
    font-family="{FONT}"
  >
    Last 30 days
  </text>

  {''.join(bars)}

  <text
    x="28"
    y="216"
    fill="{MUTED}"
    font-size="10"
    font-family="{FONT}"
  >
    {escape(start_label)}
  </text>

  <text
    x="437"
    y="216"
    fill="{MUTED}"
    font-size="10"
    font-family="{FONT}"
    text-anchor="end"
  >
    {escape(end_label)}
  </text>
"""

    return build_svg_card(
        "Contribution Streak",
        520,
        230,
        content,
    )


# ============================================================
# Main
# ============================================================

def main():
    print(
        f"Generating GitHub profile stats for @{USERNAME}"
    )

    # --------------------------------------------------------
    # Basic GitHub data
    # --------------------------------------------------------

    print("Fetching profile...")

    user_data = get_user_data()

    print("Fetching public repositories...")

    repos = get_all_public_repos()

    # --------------------------------------------------------
    # Contribution data
    # --------------------------------------------------------

    print("Fetching contribution history...")

    yearly_contributions = get_all_contributions()

    contribution_map = build_contribution_map(
        yearly_contributions
    )

    contribution_totals = aggregate_contribution_stats(
        yearly_contributions
    )

    # --------------------------------------------------------
    # Generate SVGs
    # --------------------------------------------------------

    print("Generating SVG files...")

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    stats_svg = generate_stats_svg(
        user_data,
        repos,
        contribution_totals,
    )

    streak_svg = generate_streak_svg(
        contribution_map,
        contribution_totals["contributions"],
    )

    stats_path = OUTPUT_DIR / "stats.svg"
    streak_path = OUTPUT_DIR / "streak.svg"

    stats_path.write_text(
        stats_svg,
        encoding="utf-8",
    )

    streak_path.write_text(
        streak_svg,
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    current_streak = calculate_current_streak(
        contribution_map
    )

    longest_streak = calculate_longest_streak(
        contribution_map
    )

    print()
    print("Done!")
    print(f"Stats SVG  : {stats_path}")
    print(f"Streak SVG : {streak_path}")
    print()
    print(
        "Total contributions:",
        contribution_totals["contributions"],
    )
    print(
        "Current streak:",
        current_streak,
    )
    print(
        "Longest streak:",
        longest_streak,
    )


if __name__ == "__main__":
    main()
