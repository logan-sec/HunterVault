# 🔒 HuntVault

**A local knowledge base for bug bounty hunters.** Paste a writeup URL or raw text → Claude extracts a structured technique card → you search, filter, and review it later when you're hunting.

Built this because I kept reading great writeups and then completely forgetting the techniques two weeks later. HuntVault turns writeups into actionable cards with checklists tailored to your skill level, so when you're staring at a target you can actually *find* that IDOR trick you read about.

![HuntVault Extract View](https://raw.githubusercontent.com/logan-sec/huntvault/main/screenshots/extract.png)
![HuntVault Vault View](https://raw.githubusercontent.com/logan-sec/huntvault/main/screenshots/vault.png)

## What It Does

- **Extract** — Paste a writeup URL, raw article text, or both. Claude reads it and pulls out a structured technique card with title, topic, summary, "when to use" context, and a step-by-step testing checklist.
- **Vault** — Browse all your saved cards. Filter by topic (IDOR, XSS, Auth, etc.), status, rating. Full-text search across titles, summaries, and your personal notes.
- **Review** — Open any card to see the full breakdown, update your rating, mark it as "tried", and add your own notes from testing.

Everything runs locally. Your data stays in a SQLite file on your machine.

## Quick Start

**Requirements:** Python 3.10+ and an [Anthropic API key](https://console.anthropic.com/)

```bash
# Clone the repo
git clone https://github.com/logan-sec/huntvault.git
cd huntvault

# Install dependencies
pip install -r requirements.txt

# Set up your API key
cp .env.example .env
# Edit .env and paste your real key

# Run it
python server.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) and start extracting.

## Customize Your Profile

Edit `config.py` to set your hunter profile. This changes how Claude writes the checklist on each card:

```python
HUNTER_PROFILE = {
    "handle": "hunter",
    "skill_level": "intermediate",   # beginner | intermediate | advanced
    "focus_areas": ["IDOR", "Auth", "Logic"],
}
```

- **beginner** — Checklists explain what to look for and where, assumes minimal tool familiarity
- **intermediate** — Assumes you know your tools, focuses on when the technique applies and common variations
- **advanced** — Edge cases, chaining opportunities, WAF bypasses, environment-specific gotchas

## Project Structure

```
huntvault/
├── server.py          # Python HTTP server + Claude API integration
├── index.html         # Single-file frontend (no build step)
├── config.py          # API key loader + hunter profile
├── requirements.txt   # Python dependencies
├── .env.example       # API key template
└── .gitignore
```

## How It Works Under the Hood

1. You submit a URL and/or text on the Extract page
2. If a URL is provided, the server fetches and strips the HTML down to readable text
3. The content (capped at 15k chars) gets sent to Claude with an extraction prompt tailored to your hunter profile
4. Claude returns structured JSON — title, topic, technique type, novelty rating, summary, when-to-use context, and a testing checklist
5. You review the preview card, optionally add your own rating/notes, and save it to your local SQLite vault

## Notes

- This is a local tool — there's no auth, no cloud sync, no telemetry. It's just you and your vault.
- The database file (`huntvault.db`) is created automatically on first run and excluded from git.
- API costs are minimal — each extraction is a single Claude API call.

## About

Built by [LoganSec](https://www.youtube.com/@Logan-sec). I document my bug bounty journey — the wins, the dupes, and the $0 reports. If you're into that, come hang out.

- YouTube: [@Logan-sec](https://www.youtube.com/@Logan-sec)
- X: [@LoganOpSec](https://twitter.com/LoganOpSec)
- GitHub: [logan-sec](https://github.com/logan-sec)
