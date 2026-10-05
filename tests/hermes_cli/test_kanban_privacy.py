from __future__ import annotations


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
