from jarvis import fullscreen


def test_busy_d3d_and_presentation_count_as_fullscreen():
    assert all(fullscreen.state(lambda s=s: s) for s in (2, 3, 4))


def test_normal_states_are_not_fullscreen():
    assert not any(fullscreen.state(lambda s=s: s) for s in (1, 5, 6, 7))


def test_a_failing_query_is_not_fullscreen():
    def boom():
        raise OSError

    assert fullscreen.state(boom) is False
