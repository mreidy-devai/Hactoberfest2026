"""Tests run without a model: a fake client stands in for Ollama."""
import json

import pytest

import core
from core import BusinessProfile, Review


class FakeClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def generate(self, system, prompt, temperature=0.6, json_mode=False):
        self.calls.append((system, prompt, json_mode))
        return self.responses.pop(0) if self.responses else "Thanks for the kind words about the lasagna."


def test_parse_csv_loose_headers():
    csv_text = "Reviewer,Stars,Comment\nDana,2,Cold pasta\nSam,5 stars,Great\n,3,\n"
    rs = core.parse_csv(csv_text)
    assert [(r.author, r.rating, r.text) for r in rs] == [("Dana", 2, "Cold pasta"), ("Sam", 5, "Great")]


def test_parse_csv_missing_text_column():
    with pytest.raises(ValueError):
        core.parse_csv("a,b\n1,2\n")


def test_parse_sample_file():
    with open("samples/sample_reviews.csv", "rb") as f:
        rs = core.parse_csv(f.read())
    assert len(rs) == 10 and rs[0].rating == 2 and rs[0].platform == "Google"


def test_parse_pasted():
    raw = "Dana | 2\nCold pasta.\nSlow.\n---\n5 stars\nLoved it\n---\nNo header here"
    rs = core.parse_pasted(raw)
    assert rs[0].author == "Dana" and rs[0].rating == 2 and rs[0].text == "Cold pasta.\nSlow."
    assert rs[1].rating == 5 and rs[1].text == "Loved it"
    assert rs[2].rating is None and rs[2].text == "No header here"


def test_risk_flags():
    assert "health/safety" in core.risk_flags(Review("My wife got sick after the clams", 1))
    assert "legal" in core.risk_flags(Review("calling the health department", 1))
    assert "fraud/billing" in core.risk_flags(Review("Charged twice for one order", 1))
    assert core.risk_flags(Review("Lovely lasagna", 5)) == []


def test_stats():
    s = core.stats([Review("a", 5), Review("b", 3), Review("c", 1), Review("d", None)])
    assert s["count"] == 4 and s["average"] == 3.0 and s["distribution"][5] == 1


def test_prompt_contains_policies_voice_and_signoff():
    p = BusinessProfile(name="Nonna's", owner_name="Maria", policies="Call 973-555-0101", voice_samples="Thanks so much!", sign_off="Maria")
    sp = core.build_system_prompt(p)
    assert "Nonna's" in sp and "973-555-0101" in sp and "Thanks so much!" in sp and "sign-off" in sp


def test_negative_review_gets_negative_instructions():
    assert "negative review" in core.build_review_prompt(Review("bad", 1))
    assert "positive review" in core.build_review_prompt(Review("good", 5))


def test_clean_reply():
    assert core.clean_reply('"Thanks for coming in."') == "Thanks for coming in."
    assert core.clean_reply("Here's a reply:\n**Thanks** for coming") == "Thanks for coming"


def test_draft_reply_flags_and_text():
    fc = FakeClient(["Reply: Thank you."])
    d = core.draft_reply(fc, BusinessProfile(), Review("got sick after dinner", 1))
    assert d.reply == "Thank you." and d.needs_owner


def test_draft_reply_handles_llm_error():
    class Down:
        def generate(self, *a, **k):
            raise core.LLMError("down")

    d = core.draft_reply(Down(), BusinessProfile(), Review("x", 5))
    assert d.error == "down" and d.reply == ""


def test_extract_json_tolerates_fences_and_chatter():
    out = core.extract_json('Sure!\n```json\n{"themes":[{"theme":"wait","mentions":2}],"a":{"b":1}}\n```\nDone')
    assert out["themes"][0]["theme"] == "wait" and out["a"]["b"] == 1


def test_summarize_themes_retries_on_bad_json():
    good = json.dumps({"themes": [{"theme": "Wait times", "sentiment": "negative", "mentions": 3}], "top_fix": "x", "top_strength": "y"})
    fc = FakeClient(["not json at all", good])
    out = core.summarize_themes(fc, [Review("slow", 2)])
    assert out["themes"][0]["theme"] == "Wait times" and len(fc.calls) == 2 and fc.calls[0][2] is True


def test_summarize_themes_gives_up():
    with pytest.raises(core.LLMError):
        core.summarize_themes(FakeClient(["nope", "still nope"]), [Review("slow", 2)])


def test_drafts_to_csv_roundtrip():
    d = core.draft_reply(FakeClient(["Thanks."]), BusinessProfile(), Review("got sick", 1, author="Mike"))
    out = core.drafts_to_csv([d])
    assert "Mike" in out and "yes" in out and "health/safety" in out


# ---------------------------------------------------------------- regressions

def test_person_named_sue_is_not_a_legal_threat():
    assert core.risk_flags(Review("Sue was our server and she was wonderful", 5)) == []
    assert "legal" in core.risk_flags(Review("I'm going to sue over this", 1))
    assert "legal" in core.risk_flags(Review("They were sued last year", 1))


def test_hospital_grade_clean_is_not_a_health_flag():
    assert core.risk_flags(Review("Spotless, hospital-grade clean", 5)) == []
    assert "health/safety" in core.risk_flags(Review("My son ended up in the hospital", 1))
    assert "health/safety" in core.risk_flags(Review("She was hospitalized", 1))


@pytest.mark.parametrize("raw,want", [("5", 5), ("4.5", 5), ("3.5", 4), ("2.4", 2), ("10", None), ("5/5", 5), ("0", None), ("abc", None), ("", None)])
def test_rating_parsing(raw, want):
    assert core._clean_rating(raw) == want


def test_theme_prompt_fits_context_window():
    rs = [Review("x" * 2000, 3) for _ in range(200)]
    fc = FakeClient(['{"themes":[]}'])
    out = core.summarize_themes(fc, rs)
    prompt = fc.calls[0][1]
    assert len(prompt) < 25000  # ~6k tokens, inside num_ctx=8192
    assert out["analyzed"] == 50 and out["total"] == 200


# ------------------------------------------- real HTTP path via a mock Ollama

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@pytest.fixture()
def mock_ollama():
    state = {"mode": "ok", "seen": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            self.send_response(code)
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

        def do_GET(self):
            self._send(200, {"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.1:8b"}]})

        def do_POST(self):
            state["seen"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            m = state["mode"]
            if m == "500":
                self._send(500, b"boom")
            elif m == "garbage":
                self._send(200, b"<html>not ollama</html>")
            elif m == "empty":
                self._send(200, {"message": {"content": "  "}})
            else:
                self._send(200, {"message": {"content": "Thanks for visiting."}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["client"] = core.OllamaClient(host=f"http://127.0.0.1:{srv.server_port}", timeout=2)
    yield state
    srv.shutdown()


def test_http_happy_path_and_payload(mock_ollama):
    c = mock_ollama["client"]
    assert c.is_up() and c.list_models() == ["qwen2.5:7b", "llama3.1:8b"]
    assert c.generate("sys", "hi", json_mode=True) == "Thanks for visiting."
    sent = mock_ollama["seen"][-1]
    assert sent["stream"] is False and sent["format"] == "json"
    assert sent["options"]["num_ctx"] == 8192 and sent["messages"][0]["role"] == "system"


@pytest.mark.parametrize("mode", ["500", "garbage", "empty"])
def test_http_bad_responses_become_llm_errors(mock_ollama, mode):
    mock_ollama["mode"] = mode
    with pytest.raises(core.LLMError):
        mock_ollama["client"].generate("s", "p")
    d = core.draft_reply(mock_ollama["client"], BusinessProfile(), Review("x", 5))
    assert d.error and d.reply == ""


def test_http_server_down():
    c = core.OllamaClient(host="http://127.0.0.1:1", timeout=1)
    assert not c.is_up() and c.list_models() == []
    with pytest.raises(core.LLMError, match="Can't reach Ollama"):
        c.generate("s", "p")
