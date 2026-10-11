from pathlib import Path

from jarvis.config import load_config


def test_config_agents_and_files():
    c = load_config(Path("config.toml"))
    assert c.agents["thinker"].route == "reasoning" and c.agents["thinker"].fallback
    p = c.agents["private"]
    assert p.route == "local" and p.fallback is False
    assert p.tools == ["find_files", "open_path", "search_notes", "read_file"]
    assert c.file_roots[0] == Path.home()
    assert "node_modules" in c.file_exclude


def test_config_content_settings():
    c = load_config(Path("config.toml"))
    assert c.content_private is True and "My project" in c.content_exclude
    assert (c.app_dir / "jarvis" / "brain.py").exists()
    assert c.routes["local"].model == "jarvis-local"
    r = c.agents["researcher"]
    assert r.route == "reasoning" and r.tools == ["web_search", "read_page"]
