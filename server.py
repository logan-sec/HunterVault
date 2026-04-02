import json
import os
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser

import anthropic

from config import ANTHROPIC_API_KEY, HUNTER_PROFILE

DB_PATH = "huntvault.db"
PORT = 8765

db_lock = threading.Lock()
_conn = None


def get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def init_db():
    with db_lock:
        get_db().executescript("""
            CREATE TABLE IF NOT EXISTS cards (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                title          TEXT NOT NULL,
                topic          TEXT,
                technique_type TEXT,
                novelty_rating INTEGER,
                summary        TEXT,
                when_to_use    TEXT,
                source_url     TEXT,
                notes          TEXT,
                rating         INTEGER,
                status         TEXT DEFAULT 'want to try',
                checklist      TEXT,
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        get_db().commit()


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["checklist"] = json.loads(d.get("checklist") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["checklist"] = []
    return d


def validate_config():
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY.startswith("sk-ant-YOUR"):
        print("ERROR: Set ANTHROPIC_API_KEY in config.py before starting.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    SKIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._parts.append(text)

    def get_text(self):
        return "\n".join(self._parts)


def strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    try:
        stripper.feed(html)
    except Exception:
        pass
    return stripper.get_text()


# ---------------------------------------------------------------------------
# URL fetching
# ---------------------------------------------------------------------------

def fetch_url_content(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (HuntVault/1.0; research tool)",
            "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        content_type = resp.headers.get_content_type() or ""
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read(500_000)

    if any(b in content_type for b in ("image/", "video/", "audio/", "application/pdf")):
        raise ValueError(f"Unsupported content type: {content_type}")

    text = raw.decode(charset, errors="replace")

    if "html" in content_type:
        text = strip_html(text)

    return text


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------

def call_claude(prompt: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def build_extraction_prompt(content: str, profile: dict) -> str:
    skill = profile.get("skill_level", "intermediate")
    focus = ", ".join(profile.get("focus_areas", []))
    handle = profile.get("handle", "hunter")

    if skill == "beginner":
        checklist_guidance = "concrete steps, explain what to look for and where, assume minimal tool familiarity"
    elif skill == "advanced":
        checklist_guidance = "focus on edge cases, chaining opportunities, WAF bypasses, and environment-specific variations"
    else:
        checklist_guidance = "assume tool familiarity, focus on conditions that make the technique applicable and common variations"

    return f"""You are a bug bounty knowledge extraction assistant. Extract the core offensive technique from the content below and produce a structured knowledge card.

HUNTER PROFILE:
  Handle: {handle}
  Skill Level: {skill}
  Focus Areas: {focus}

INSTRUCTIONS:
- Extract the single most important offensive technique from the content.
- The checklist items must be SPECIFIC and ACTIONABLE — not generic advice like "test for XSS".
- Tailor checklist items to the hunter's skill level: {checklist_guidance}
- novelty_rating reflects how unusual or underused the technique is (1=well-known/common, 5=rare/novel/clever).
- Return ONLY valid JSON. No markdown fences, no explanation, no text outside the JSON object.

REQUIRED OUTPUT FORMAT:
{{
  "title": "concise technique name (max 60 chars)",
  "topic": "one of: XSS | SSRF | IDOR | Auth | SQLi | RCE | LFI | CSRF | Logic | Recon | Other",
  "technique_type": "one of: bypass | injection | escalation | enumeration | discovery | chaining | misconfig | other",
  "novelty_rating": <integer 1-5>,
  "summary": "2-3 sentences describing what the technique is and why it works",
  "when_to_use": "specific application conditions, indicators, or target characteristics that make this technique applicable",
  "checklist": [
    "item 1: specific actionable step",
    "item 2: specific actionable step",
    "item 3: specific actionable step"
  ]
}}

CONTENT TO ANALYZE:
\"\"\"
{content}
\"\"\""""


def extract_json_from_response(text: str) -> dict:
    # Try markdown code fence first
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # Fall back to first {...} block
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    raise ValueError("No JSON object found in Claude response")


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

UPDATABLE_FIELDS = {
    "title", "topic", "technique_type", "novelty_rating",
    "summary", "when_to_use", "source_url",
    "notes", "rating", "status", "checklist",
}


class HuntVaultHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass  # suppress access log

    def send_json(self, data, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, msg: str, status: int = 400):
        self.send_json({"error": msg}, status)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            raw = self.rfile.read(length)
        else:
            raw = self.rfile.read()
        return json.loads(raw) if raw else {}

    def route(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        m = self.command

        if m == "GET" and path == "/":
            return self.handle_index()
        if m == "GET" and path == "/api/cards":
            return self.handle_get_cards(qs)
        if m == "POST" and path == "/api/cards":
            return self.handle_create_card()
        if m == "POST" and path == "/api/extract":
            return self.handle_extract()
        if m in ("PUT", "DELETE"):
            match = re.match(r"^/api/cards/(\d+)$", path)
            if match:
                card_id = int(match.group(1))
                if m == "PUT":
                    return self.handle_update_card(card_id)
                return self.handle_delete_card(card_id)

        self.send_error_json("Not found", 404)

    def do_GET(self):
        self.route()

    def do_POST(self):
        self.route()

    def do_PUT(self):
        self.route()

    def do_DELETE(self):
        self.route()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------

    def handle_index(self):
        index_path = os.path.join(os.path.dirname(__file__), "index.html")
        try:
            with open(index_path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self.send_error_json("index.html not found", 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_get_cards(self, qs):
        topic  = qs.get("topic",  [None])[0]
        status = qs.get("status", [None])[0]
        rating = qs.get("rating", [None])[0]
        q      = qs.get("q",      [None])[0]

        sql    = "SELECT * FROM cards WHERE 1=1"
        params = []

        if topic:
            sql += " AND topic = ?"
            params.append(topic)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if rating:
            try:
                sql += " AND rating = ?"
                params.append(int(rating))
            except ValueError:
                pass
        if q:
            like = f"%{q}%"
            sql += " AND (title LIKE ? OR summary LIKE ? OR notes LIKE ? OR technique_type LIKE ?)"
            params.extend([like, like, like, like])

        sql += " ORDER BY created_at DESC"

        with db_lock:
            rows = get_db().execute(sql, params).fetchall()

        self.send_json([row_to_dict(r) for r in rows])

    def handle_create_card(self):
        data = self.read_body()
        title = (data.get("title") or "").strip()

        if not title:
            # Fallback: use URL domain or content snippet
            url = data.get("source_url", "")
            if url:
                try:
                    title = urllib.parse.urlparse(url).netloc or "Untitled"
                except Exception:
                    title = "Untitled"
            else:
                title = "Untitled"
            data["title"] = title

        checklist = data.get("checklist", [])
        if isinstance(checklist, list):
            checklist = json.dumps(checklist)

        with db_lock:
            conn = get_db()
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO cards
                  (title, topic, technique_type, novelty_rating, summary,
                   when_to_use, source_url, notes, rating, status, checklist)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    data.get("title"),
                    data.get("topic"),
                    data.get("technique_type"),
                    data.get("novelty_rating"),
                    data.get("summary"),
                    data.get("when_to_use"),
                    data.get("source_url"),
                    data.get("notes"),
                    data.get("rating") or None,
                    data.get("status", "want to try"),
                    checklist,
                ),
            )
            conn.commit()
            card_id = c.lastrowid
            row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()

        self.send_json(row_to_dict(row), status=201)

    def handle_update_card(self, card_id: int):
        data = self.read_body()
        set_parts, params = [], []

        for field in UPDATABLE_FIELDS:
            if field in data:
                val = data[field]
                if field == "checklist" and isinstance(val, list):
                    val = json.dumps(val)
                set_parts.append(f"{field} = ?")
                params.append(val)

        if not set_parts:
            return self.send_error_json("No valid fields to update")

        set_parts.append("updated_at = CURRENT_TIMESTAMP")
        params.append(card_id)

        with db_lock:
            conn = get_db()
            conn.execute(
                f"UPDATE cards SET {', '.join(set_parts)} WHERE id=?", params
            )
            conn.commit()
            row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()

        if not row:
            return self.send_error_json("Card not found", 404)
        self.send_json(row_to_dict(row))

    def handle_delete_card(self, card_id: int):
        with db_lock:
            conn = get_db()
            row = conn.execute("SELECT id FROM cards WHERE id=?", (card_id,)).fetchone()
            if not row:
                return self.send_error_json("Card not found", 404)
            conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
            conn.commit()
        self.send_json({"deleted": card_id})

    def handle_extract(self):
        data = self.read_body()
        url  = (data.get("url") or "").strip()
        text = (data.get("text") or "").strip()

        if not url and not text:
            return self.send_error_json("Provide url, text, or both")

        fetched_text = ""
        if url:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            try:
                fetched_text = fetch_url_content(url)
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                return self.send_error_json(f"URL fetch failed: {e}")
            except ValueError as e:
                return self.send_error_json(str(e))
            except Exception as e:
                return self.send_error_json(f"URL fetch failed: {e}")

        combined = text or fetched_text
        if text and fetched_text:
            combined = f"{text}\n\n---SOURCE PAGE---\n{fetched_text}"

        MAX_CHARS = 15000
        if len(combined) > MAX_CHARS:
            combined = combined[:MAX_CHARS] + "\n...[content truncated]"

        prompt = build_extraction_prompt(combined, HUNTER_PROFILE)

        try:
            raw_response = call_claude(prompt)
        except Exception as e:
            return self.send_error_json(f"Claude API error: {e}", 500)

        try:
            card = extract_json_from_response(raw_response)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[extract] Claude raw response:\n{raw_response}", file=sys.stderr)
            return self.send_error_json(f"Claude returned invalid JSON: {e}", 500)

        if url:
            card["source_url"] = url

        # Ensure title fallback
        if not card.get("title"):
            if url:
                card["title"] = urllib.parse.urlparse(url).netloc or "Untitled"
            elif combined:
                card["title"] = combined[:60].strip()
            else:
                card["title"] = "Untitled"

        self.send_json(card)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    validate_config()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), HuntVaultHandler)
    print(f"HuntVault running → http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
