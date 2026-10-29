from wattnot.proof.profile import PROFILES, resolve


def test_longrun_profile_has_60s_windows():
    p = resolve("longrun")
    assert 60 <= p.metered_seconds() <= 70
    assert p.reps == 3 and p.counterbalance and p.burn_in == 1 and p.control_blocks >= 2


def test_quick_is_still_run2():
    assert PROFILES["quick"].metered_seconds() < 8
