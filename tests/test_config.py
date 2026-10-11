from pathlib import Path

from jarvis.config import load_config


def test_config_agents_and_files():
    c = load_config(Path("config.toml"))
    assert c.agents["thinker"].route == "reasoning" and c.agents["thinker"].fallback
    p = c.agents["private"]
    assert p.route == "local" and p.fallback is False and p.tools == ["find_files", "open_path"]
    assert c.file_roots[0] == Path.home()
    assert "node_modules" in c.file_exclude
