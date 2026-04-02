import os

# ─── API Key ───────────────────────────────────────────────────────
# Set your Anthropic API key in a .env file or as an environment variable.
#   ANTHROPIC_API_KEY=sk-ant-api03-...
#
# Never commit your real key. See .env.example for the template.
# ───────────────────────────────────────────────────────────────────

# Minimal .env loader — no extra dependencies
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ─── Hunter Profile ────────────────────────────────────────────────
# Customize this to your skill level and focus areas.
# The extraction prompt uses this to tailor checklist detail.
# ───────────────────────────────────────────────────────────────────

HUNTER_PROFILE = {
    "handle": "hunter",
    "skill_level": "intermediate",   # beginner | intermediate | advanced
    "focus_areas": ["IDOR", "Auth", "Logic"],
}
