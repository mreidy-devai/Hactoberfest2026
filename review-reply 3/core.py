"""Review Reply core engine.

Everything here talks only to a local Ollama server (default localhost:11434).
No third-party Python dependencies: standard library only.
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"

# Words that mean the owner should read the review before anything is posted.
RISK_PATTERNS = {
    "health/safety": r"\b(food poisoning|got sick|sick after|made me sick|allergic|allergy|injur\w+|hospitali[sz]ed|emergency room|(went|taken|rushed|sent|ended up in|landed in) (to )?(the )?(hospital|ER)|ER visit|burn(ed|t) me|unsafe)\b",
    # (?-i:sue) is case-sensitive so a person named Sue isn't flagged as a legal threat.
    "legal": r"\b(lawyer|attorney|lawsuit|(?-i:sue)|sued|suing|legal action|report(ing)? you|health department|BBB|better business bureau)\b",
    "discrimination": r"\b(racist|racism|sexist|discriminat\w+|homophobic|harass\w+)\b",
    "fraud/billing": r"\b(scam|fraud|overcharg\w+|charged twice|double charged|stole|stolen|rip-?off)\b",
}


@dataclass
class BusinessProfile:
    name: str = ""
    kind: str = ""  # e.g. "family Italian restaurant", "hair salon"
    owner_name: str = ""
    tone: str = "warm, direct, professional"
    sign_off: str = ""
    voice_samples: str = ""  # past replies the owner wrote, pasted in
    policies: str = ""  # e.g. "Invite unhappy customers to call 973-555-0101. Never promise refunds publicly."


@dataclass
class Review:
    text: str
    rating: int | None = None
    author: str = ""
    platform: str = ""
    date: str = ""


@dataclass
class Draft:
    review: Review
    reply: str
    flags: list[str] = field(default_factory=list)
    error: str = ""
    rev: int = 0  # bumped on every redo so the UI text box is rebuilt with the new text

    @property
    def needs_owner(self) -> bool:
        return bool(self.flags)


# ----------------------------------------------------------------- parsing

_COLS = {
    "text": ("text", "review", "comment", "body", "content", "review text", "review_text"),
    "rating": ("rating", "stars", "star", "score", "star rating"),
    "author": ("author", "name", "reviewer", "user", "reviewer name"),
    "platform": ("platform", "source", "site"),
    "date": ("date", "created", "time", "review date"),
}


def _clean_rating(value) -> int | None:
    if value is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(value))
    if not m:
        return None
    n = int(float(m.group(0)) + 0.5)  # round half up: 4.5 -> 5 (Python's round() gives 4)
    return n if 1 <= n <= 5 else None


def parse_csv(data: str | bytes) -> list[Review]:
    """Parse a CSV export. Column names are matched loosely (rating/stars, text/review...)."""
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(data))
    if not reader.fieldnames:
        return []
    lookup: dict[str, str] = {}
    for canonical, aliases in _COLS.items():
        for f in reader.fieldnames:
            if f and f.strip().lower() in aliases:
                lookup[canonical] = f
                break
    if "text" not in lookup:
        raise ValueError(
            "Couldn't find a review text column. Name it one of: " + ", ".join(_COLS["text"])
        )
    reviews = []
    for row in reader:
        text = (row.get(lookup["text"]) or "").strip()
        if not text:
            continue
        reviews.append(
            Review(
                text=text,
                rating=_clean_rating(row.get(lookup.get("rating", ""))),
                author=(row.get(lookup.get("author", "")) or "").strip(),
                platform=(row.get(lookup.get("platform", "")) or "").strip(),
                date=(row.get(lookup.get("date", "")) or "").strip(),
            )
        )
    return reviews


def parse_pasted(raw: str) -> list[Review]:
    """Parse pasted reviews. Separate reviews with a line of '---'.

    An optional first line of the form 'Name | 4' (or just '4 stars') sets author and rating.
    """
    reviews = []
    for block in re.split(r"^\s*-{3,}\s*$", raw, flags=re.MULTILINE):
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        author, rating = "", None
        head = lines[0].strip()
        m = re.fullmatch(r"(?:(.+?)\s*\|\s*)?(\d(?:\.\d)?)\s*(?:stars?|/5)?", head, flags=re.I)
        if m and len(lines) > 1:
            author = (m.group(1) or "").strip()
            rating = _clean_rating(m.group(2))
            block = "\n".join(lines[1:]).strip()
        reviews.append(Review(text=block, rating=rating, author=author))
    return reviews


# ------------------------------------------------------------------- flags

def risk_flags(review: Review) -> list[str]:
    return [label for label, pat in RISK_PATTERNS.items() if re.search(pat, review.text, flags=re.I)]


def stats(reviews: list[Review]) -> dict:
    rated = [r.rating for r in reviews if r.rating]
    dist = Counter(rated)
    return {
        "count": len(reviews),
        "average": round(sum(rated) / len(rated), 2) if rated else None,
        "distribution": {s: dist.get(s, 0) for s in (5, 4, 3, 2, 1)},
        "needs_owner": sum(1 for r in reviews if risk_flags(r)),
    }


# --------------------------------------------------------------- LLM client

class LLMError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, host: str = OLLAMA_URL, model: str = DEFAULT_MODEL, timeout: int = 300, num_ctx: int = 8192):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Ollama's default context window is small (2048 to 4096 tokens) and silently truncates
        # longer prompts, which would drop reviews from the theme summary.
        self.num_ctx = num_ctx

    def _request(self, path: str, payload: dict | None = None, timeout: int | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.host + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode())
        except ValueError as e:  # includes JSONDecodeError and UnicodeDecodeError
            raise LLMError("The server at that address didn't answer like Ollama. Check the address.") from e
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise LLMError(f"Ollama returned {e.code}: {body[:200]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise LLMError(
                "Can't reach Ollama. Make sure it's running (open the Ollama app, or run `ollama serve`)."
            ) from e

    def is_up(self) -> bool:
        try:
            self._request("/api/tags", timeout=3)
            return True
        except LLMError:
            return False

    def list_models(self) -> list[str]:
        try:
            return [m["name"] for m in self._request("/api/tags", timeout=5).get("models", [])]
        except LLMError:
            return []

    def generate(self, system: str, prompt: str, temperature: float = 0.6, json_mode: bool = False) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if json_mode:
            payload["format"] = "json"
        out = self._request("/api/chat", payload)
        content = (out.get("message", {}).get("content") or "").strip()
        if not content:
            raise LLMError("The model returned an empty reply. Try again, or pick a different model.")
        return content


# ----------------------------------------------------------------- prompts

def _tone_for(review: Review) -> str:
    r = review.rating
    if r is None:
        return "Judge the sentiment from the text and respond accordingly."
    if r >= 4:
        return (
            "This is a positive review. Thank them for something SPECIFIC they mentioned. "
            "Be genuine, not gushing. Invite them back in a natural way."
        )
    if r == 3:
        return (
            "This is a mixed review. Thank them, acknowledge the specific thing that fell short, "
            "and say what you'll look into. Don't be defensive."
        )
    return (
        "This is a negative review. Open with a sincere, specific acknowledgment of what went wrong "
        "(no 'we're sorry you feel that way'). Do not argue, excuse, or blame the customer. "
        "Take it offline using the contact details in the policies if any are given. "
        "Do not promise refunds, discounts, or compensation unless the policies allow it."
    )


def build_system_prompt(profile: BusinessProfile) -> str:
    parts = [
        "You write public replies to customer reviews on behalf of a small local business owner.",
        f"Business: {profile.name or 'the business'}" + (f" ({profile.kind})" if profile.kind else "") + ".",
    ]
    if profile.owner_name:
        parts.append(f"The owner is {profile.owner_name}; write in first person as the owner or their team.")
    parts.append(f"Tone: {profile.tone}.")
    parts.append(
        "Rules:\n"
        "- 40 to 90 words. Plain text only. No headings, no markdown, no quotation marks around the reply.\n"
        "- Mention at least one concrete detail from the review so it doesn't read like a template.\n"
        "- Never invent facts: no made-up staff names, menu items, dates, prices, or promises.\n"
        "- Never use filler like 'valued customer', 'we strive for excellence', or 'your feedback is important to us'.\n"
        "- No emojis unless the owner's own sample replies use them.\n"
        "- Don't repeat the reviewer's name more than once.\n"
        "- If the review alleges illness, injury, discrimination, or illegal conduct, do not admit fault or "
        "speculate; express concern and invite them to contact the business directly."
    )
    if profile.policies.strip():
        parts.append("Business policies to follow:\n" + profile.policies.strip())
    if profile.voice_samples.strip():
        parts.append(
            "Match the voice of these real replies written by the owner (style only, don't copy sentences):\n"
            + profile.voice_samples.strip()[:3000]
        )
    if profile.sign_off.strip():
        parts.append(f"End the reply with exactly this sign-off on its own line: {profile.sign_off.strip()}")
    parts.append("Output ONLY the reply text.")
    return "\n\n".join(parts)


def build_review_prompt(review: Review) -> str:
    rating = f"{review.rating} out of 5 stars" if review.rating else "no star rating given"
    who = f"Reviewer: {review.author}\n" if review.author else ""
    plat = f"Platform: {review.platform}\n" if review.platform else ""
    return f"{plat}{who}Rating: {rating}\n\nReview:\n{review.text}\n\n{_tone_for(review)}\n\nWrite the reply."


def clean_reply(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^(here('s| is)[^\n:]*:|reply:|response:)\s*", "", text, flags=re.I).strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'“”":
        text = text[1:-1].strip()
    text = re.sub(r"\*\*|__", "", text)
    return text


# -------------------------------------------------------------- generation

def draft_reply(client, profile: BusinessProfile, review: Review) -> Draft:
    flags = risk_flags(review)
    try:
        raw = client.generate(build_system_prompt(profile), build_review_prompt(review))
    except LLMError as e:
        return Draft(review=review, reply="", flags=flags, error=str(e))
    return Draft(review=review, reply=clean_reply(raw), flags=flags)


def extract_json(text: str):
    """Pull the first JSON object out of model output, tolerating code fences and chatter."""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unterminated JSON object")


THEME_SYSTEM = (
    "You analyze customer reviews for a small business owner. Find recurring themes across reviews. "
    "Only report a theme if it is supported by the review text. Respond with JSON only, in this shape:\n"
    '{"themes":[{"theme":"short label","sentiment":"positive|negative|mixed","mentions":<int>,'
    '"evidence":"a short phrase quoted from one review"}],'
    '"top_fix":"the single most valuable thing the owner could change, in one sentence",'
    '"top_strength":"the thing customers praise most, in one sentence"}\n'
    "Give 3 to 6 themes, ordered by mentions."
)


def summarize_themes(client, reviews: list[Review], max_reviews: int = 50) -> dict:
    """Worst case is 50 reviews x 400 chars, about 5.5k tokens, which fits the 8192-token context."""
    sample = reviews[:max_reviews]
    lines = []
    for i, r in enumerate(sample, 1):
        stars = f"{r.rating}★" if r.rating else "?★"
        lines.append(f"{i}. [{stars}] {r.text.replace(chr(10), ' ')[:400]}")
    prompt = "Reviews:\n" + "\n".join(lines) + "\n\nReturn the JSON."
    last_err = None
    for _ in range(2):  # small models occasionally emit bad JSON; retry once
        raw = client.generate(THEME_SYSTEM, prompt, temperature=0.2, json_mode=True)
        try:
            data = extract_json(raw)
            data.setdefault("themes", [])
            data["analyzed"], data["total"] = len(sample), len(reviews)
            return data
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
    raise LLMError(f"The model returned something unreadable for the theme summary ({last_err}). Try again or pick a larger model.")


def drafts_to_csv(drafts: list[Draft]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["author", "platform", "rating", "review", "draft_reply", "needs_owner_review", "flags"])
    for d in drafts:
        r = d.review
        w.writerow([r.author, r.platform, r.rating or "", r.text, d.reply, "yes" if d.needs_owner else "", "; ".join(d.flags)])
    return buf.getvalue()
