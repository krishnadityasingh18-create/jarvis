from jarvis import lyrics


def test_parse_reads_lrc_tags_including_repeats_and_blank_breaks():
    assert lyrics.parse("[00:01.50] a\n[01:02.00][01:10.25]b\nno tag\n[00:03.00]") == \
        [(1.5, "a"), (3.0, ""), (62.0, "b"), (70.25, "b")]


def test_clean_strips_youtube_noise_but_keeps_real_parentheses():
    assert lyrics.clean("Arctic Monkeys - I Wanna Be Yours (Official Video)", "Arctic MonkeysVEVO") == \
        ("I Wanna Be Yours", "Arctic Monkeys")
    assert lyrics.clean("Flowers [Lyric Video]", "Miley Cyrus - Topic") == ("Flowers", "Miley Cyrus")
    assert lyrics.clean("Love (Remix)", "X") == ("Love (Remix)", "X")


def test_fetch_caches_answers_but_not_network_failures(monkeypatch):
    calls = []
    monkeypatch.setattr(lyrics, "_cache", {})
    monkeypatch.setattr(lyrics, "_lookup", lambda t, a, d: calls.append(t) or (None if t == "Down" else [(1.0, "x")]))
    assert lyrics.fetch("Song", "A") == lyrics.fetch("Song", "A") == [(1.0, "x")]
    assert lyrics.fetch("Down", "A") == [] and lyrics.fetch("Down", "A") == []
    assert calls == ["Song", "Down", "Down"]
