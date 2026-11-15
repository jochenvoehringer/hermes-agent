from __future__ import annotations

from pathlib import Path
import pytest
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc

@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_price_context_pseudonymizes_contact_data_but_keeps_company_names():
    from hermes_cli.kanban_privacy import pseudonymize_price_context

    source = (
        "GESOBAU AG: Kontakt Clara Zrenner, clara.zrenner@example.com, "
        "+49 30 1234567"
    )
    result = pseudonymize_price_context(source)

    assert "Clara Zrenner" not in result
    assert "clara.zrenner@example.com" not in result
    assert "+49 30 1234567" not in result
    assert "GESOBAU AG" in result
    assert "[PERSON_1]" in result
    assert "[EMAIL_1]" in result
    assert "[PHONE_1]" in result


def test_secret_redaction_can_preserve_phone_numbers_for_local_kanban():
    from agent.redact import redact_sensitive_text

    phone = "+491701234567"
    secret = "ghp_" + "A" * 40
    result = redact_sensitive_text(
        f"Kontakt {phone}; credential {secret}",
        force=True,
        redact_phone_numbers=False,
    )

    assert phone in result
    assert secret not in result


def test_price_worker_context_pseudonymizes_contact_pii(kanban_home):
    body = "Kontakt Clara Zrenner, clara.zrenner@example.com, +491701234567"
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Preis für Clara Zrenner", body=body, assignee="preis")
        context = kb.build_worker_context(conn, task_id)
        stored = kb.get_task(conn, task_id)

    assert stored is not None and stored.body == body
    assert "Clara Zrenner" not in context
    assert "clara.zrenner@example.com" not in context
    assert "+491701234567" not in context
    assert "[PERSON_1]" in context


def test_other_worker_context_preserves_contact_pii(kanban_home):
    body = "Kontakt Clara Zrenner, clara.zrenner@example.com, +491701234567"
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="Research", body=body, assignee="research")
        context = kb.build_worker_context(conn, task_id)

    assert body in context
