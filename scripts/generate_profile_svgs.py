import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import requests


USERNAME = os.environ["GITHUB_USERNAME"]
TOKEN = os.environ["GITHUB_TOKEN"]

BG = "#1a1b27"
BORDER = "#2f3441"
TITLE = "#70a5fd"
TEXT = "#c0caf5"
MUTED = "#9aa5ce"
ACCENT = "#bb9af7"
ACCENT_2 = "#7aa2f7"
ZERO_BAR = "#414868"
FONT = "Segoe UI, Ubuntu, Sans-Serif"


session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": f"{USERNAME}-profile-stats-generator",
})


def github_rest(path, params=None):
    url = f"https://api.github.com{path}"
    response = session.get(url, params=params)
    response.raise_for_status()
    return response.json()


def github_graphql(query, variables):
    response = session.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
    )
    response.raise_for_status()
    data = response.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def get_user_data():
    return github_rest(f"/users/{USERNAME}")


def get_all_repos():
    repos = []
    page = 1

    while True:
        data = github_rest(
            f"/users/{USERNAME}/repos",
            params={
                "per_page": 100,
                "page": page,
                "type": "owner",
                "sort": "updated",
            },
        )

        if not data:
            break

        repos.extend(data)

        if len(data) < 100:
            break

        page += 1

    return [repo for repo in repos if not repo.get("fork", False)]


def get_contributions():
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=730)).replace(hour=0, minute=0, second=0, microsecond=0)

    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        currentYear: contributionsCollection {
          contributionCalendar {
            totalContributions
          }
          totalCommitContributions
          totalIssueContributions
          totalPullRequestContributions
          totalPullRequestReviewContributions
        }
        rolling: contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """

    data = github_graphql(
        query,
        {
            "login": USERNAME,
            "from": from_date.isoformat().replace("+00:00", "Z"),
            "to": now.isoformat().replace("+00:00", "Z"),
        },
    )

    return data["user"]


def fmt_number(value):
    return f"{value:,}"


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def compute_streaks(days):
    sorted_days = sorted(days, key=lambda item: item["date"])

    longest = 0
    running = 0

    for day in sorted_days:
        if day["contributionCount"] > 0:
            running += 1
            longest = max(longest, running)
        else:
            running = 0

    last_active_index = None
    for i in range(len(sorted_days) - 1, -1, -1):
        if sorted_days[i]["contributionCount"] > 0:
            last_active_index = i
            break

    if last_active_index is None:
        return 0, 0, None

    current = 0
    for i in range(last_active_index, -1, -1):
        if sorted_days[i]["contributionCount"] > 0:
            current += 1
        else:
            break

    last_active = parse_date(sorted_days[last_active_index]["date"])
    return current, longest, last_active


def format_pretty_date(value):
    if value is None:
        return "No activity"
    return value.strftime("%d %b %Y")


def build_svg_card(title, width, height, content):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
  <rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="14" fill="{BG}" stroke="{BORDER}" stroke-width="1.5"/>
  <text x="24" y="36" fill="{TITLE}" font-size="20" font-family="{FONT}" font-weight="700">{escape(title)}</text>
  {content}
</svg>
"""


def stat_item(x, y, label, value):
    return f"""
  <text x="{x}" y="{y}" fill="{MUTED}" font-size="12" font-family="{FONT}">{escape(label)}</text>
  <text x="{x}" y="{y + 24}" fill="{TEXT}" font-size="24" font-family="{FONT}" font-weight="700">{escape(value)}</text>
"""


def generate_stats_svg(user_data, repos, contributions):
    stars = sum(repo.get("stargazers_count", 0) for repo in repos)
    public_repos = user_data.get("public_repos", 0)
    followers = user_data.get("followers", 0)

    current_year = contributions["currentYear"]

    items = [
        ("Public Repos", fmt_number(public_repos)),
        ("Total Stars", fmt_number(stars)),
        ("Followers", fmt_number(followers)),
        ("Contributions (YTD)", fmt_number(current_year["contributionCalendar"]["totalContributions"])),
        ("Commits (YTD)", fmt_number(current_year["totalCommitContributions"])),
        ("PRs (YTD)", fmt_number(current_year["totalPullRequestContributions"])),
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
    for (label, value), (x, y) in zip(items, positions):
        content.append(stat_item(x, y, label, value))

    return build_svg_card("GitHub Stats", 520, 220, "".join(content))


def generate_streak_svg(contributions):
    rolling = contributions["rolling"]["contributionCalendar"]
    days = []
    for week in rolling["weeks"]:
        for day in week["contributionDays"]:
            days.append(day)

    current_streak, longest_streak, last_active = compute_streaks(days)
    recent_days = days[-30:]
    counts = [day["contributionCount"] for day in recent_days]
    max_count = max(counts) if counts else 1
    if max_count == 0:
        max_count = 1

    chart_left = 28
    chart_bottom = 192
    bar_width = 10
    bar_gap = 4
    max_bar_height = 55

    bars = []
    for i, day in enumerate(recent_days):
        count = day["contributionCount"]
        if count == 0:
            bar_height = 4
            color = ZERO_BAR
        else:
            bar_height = max(6, round((count / max_count) * max_bar_height))
            color = ACCENT

        x = chart_left + i * (bar_width + bar_gap)
        y = chart_bottom - bar_height

        bars.append(
            f'<rect x="{x}" y="{y}" width="{bar_width}" height="{bar_height}" rx="3" fill="{color}" />'
        )

    start_label = format_pretty_date(parse_date(recent_days[0]["date"])) if recent_days else ""
    end_label = format_pretty_date(parse_date(recent_days[-1]["date"])) if recent_days else ""

    content = f"""
  <text x="28" y="68" fill="{MUTED}" font-size="12" font-family="{FONT}">Current Streak</text>
  <text x="28" y="92" fill="{TEXT}" font-size="24" font-family="{FONT}" font-weight="700">{current_streak} days</text>

  <text x="280" y="68" fill="{MUTED}" font-size="12" font-family="{FONT}">Longest Streak</text>
  <text x="280" y="92" fill="{TEXT}" font-size="24" font-family="{FONT}" font-weight="700">{longest_streak} days</text>

  <text x="28" y="126" fill="{MUTED}" font-size="12" font-family="{FONT}">Last Active</text>
  <text x="28" y="150" fill="{TEXT}" font-size="18" font-family="{FONT}" font-weight="700">{format_pretty_date(last_active)}</text>

  <text x="280" y="126" fill="{MUTED}" font-size="12" font-family="{FONT}">Contributions (2y)</text>
  <text x="280" y="150" fill="{TEXT}" font-size="18" font-family="{FONT}" font-weight="700">{fmt_number(rolling["totalContributions"])}</text>

  <text x="28" y="176" fill="{MUTED}" font-size="12" font-family="{FONT}">Last 30 days</text>
  {''.join(bars)}
  <text x="28" y="212" fill="{MUTED}" font-size="10" font-family="{FONT}">{escape(start_label)}</text>
  <text x="420" y="212" fill="{MUTED}" font-size="10" font-family="{FONT}">{escape(end_label)}</text>
"""

    return build_svg_card("Contribution Streak", 520, 230, content)


def main():
    user_data = get_user_data()
    repos = get_all_repos()
    contributions = get_contributions()

    profile_dir = Path("profile")
    profile_dir.mkdir(parents=True, exist_ok=True)

    stats_svg = generate_stats_svg(user_data, repos, contributions)
    streak_svg = generate_streak_svg(contributions)

    (profile_dir / "stats.svg").write_text(stats_svg, encoding="utf-8")
    (profile_dir / "streak.svg").write_text(streak_svg, encoding="utf-8")


if __name__ == "__main__":
    main()
