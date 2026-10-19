"""UI regression tests using Streamlit's headless AppTest. Skipped if streamlit isn't installed."""
import itertools

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

import core


@pytest.fixture()
def app(monkeypatch):
    counter = itertools.count(1)
    monkeypatch.setattr(core.OllamaClient, "is_up", lambda self: True)
    monkeypatch.setattr(core.OllamaClient, "list_models", lambda self: ["qwen2.5:7b"])
    monkeypatch.setattr(
        core.OllamaClient,
        "generate",
        lambda self, s, p, temperature=0.6, json_mode=False: f"REPLY {next(counter)}",
    )
    at = AppTest.from_file("app.py", default_timeout=30)
    at.session_state["reviews"] = core.parse_csv(open("samples/sample_reviews.csv", "rb").read())[:3]
    at.run()
    return at


def boxes(at):
    return [t.value for t in at.text_area if t.label.startswith("Draft reply")]


def click(at, label):
    next(b for b in at.button if b.label.startswith(label)).click().run()


def test_draft_all_then_redo_one_shows_new_text(app):
    click(app, "Draft replies")
    assert boxes(app) == ["REPLY 1", "REPLY 2", "REPLY 3"]
    click(app, "Redo this one")  # first review
    assert boxes(app) == ["REPLY 4", "REPLY 2", "REPLY 3"]
    assert [d.reply for d in app.session_state["drafts"]] == boxes(app)


def test_draft_all_twice_replaces_text(app):
    click(app, "Draft replies")
    click(app, "Draft replies")
    assert boxes(app) == ["REPLY 4", "REPLY 5", "REPLY 6"]


def test_edit_survives_rerun_and_reaches_export(app):
    click(app, "Draft replies")
    next(t for t in app.text_area if t.label.startswith("Draft reply")).set_value("My own wording").run()
    assert boxes(app)[0] == "My own wording"
    assert app.session_state["drafts"][0].reply == "My own wording"
    assert "My own wording" in core.drafts_to_csv(app.session_state["drafts"])


def test_no_exceptions_and_flags_shown(app):
    click(app, "Draft replies")
    assert not app.exception
