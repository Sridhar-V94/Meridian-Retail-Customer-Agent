from pathlib import Path

import setup_env


def test_setup_env_writes_model_correctly_without_printing_secrets(monkeypatch, tmp_path, capsys):
    chosen_model = "opencode/test-model"

    # Make setup_env write to tmp project dir by monkeypatching __file__ resolution
    fake_script = tmp_path / "setup_env.py"
    fake_script.write_text("# placeholder", encoding="utf-8")
    monkeypatch.setattr(setup_env, "__file__", str(fake_script))

    monkeypatch.setattr("builtins.input", lambda prompt="": chosen_model)

    setup_env.main()

    env_path = tmp_path / ".env"
    assert env_path.exists()
    content = env_path.read_text(encoding="utf-8")
    assert f"APP_OPENCODE_MODEL={chosen_model}" in content
    assert "APP_ANTHROPIC_API_KEY" not in content

    captured = capsys.readouterr()
    assert chosen_model not in captured.out
    assert chosen_model not in captured.err
    assert ".env" in captured.out  # confirms it reported writing file


def test_setup_env_uses_default_model_when_blank(monkeypatch, tmp_path, capsys):
    fake_script = tmp_path / "setup_env.py"
    fake_script.write_text("# placeholder", encoding="utf-8")
    monkeypatch.setattr(setup_env, "__file__", str(fake_script))

    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    setup_env.main()

    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"APP_OPENCODE_MODEL={setup_env.DEFAULT_MODEL}" in content
    captured = capsys.readouterr()
    assert ".env" in captured.out
