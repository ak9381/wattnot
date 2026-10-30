from wattnot.proof.teardown import plan_teardown


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _kw(c, **k):
    base = dict(sleep=c.sleep, clock=c, why="test")
    base.update(k)
    return base


def test_blip_then_back_never_deletes_and_copies():
    c = Clock()
    seq = iter([False, False, True])
    pulls = []
    d = plan_teardown(ssh_alive=lambda: next(seq), api_state=lambda: "running",
                      pull=lambda: pulls.append(1) or True, **_kw(c))
    assert not d.delete and d.vm_state == "alive" and d.copied and pulls


def test_run3_case_two_minutes_lost_is_not_dead():
    c = Clock()
    # SSH down for 3 minutes, then back: RUN3 would have deleted at ~2 min
    d = plan_teardown(ssh_alive=lambda: c.t >= 180, api_state=lambda: "running",
                      pull=lambda: True, **_kw(c))
    assert not d.delete


def test_dead_only_after_window_and_min_probes():
    c = Clock()
    d = plan_teardown(ssh_alive=lambda: False, api_state=lambda: "running", pull=lambda: True,
                      **_kw(c, dead_window_s=900, probe_every_s=60, min_probes=5))
    assert d.delete and d.vm_state == "dead" and c.t >= 900 and len(d.probes) >= 5


def test_api_unreadable_is_not_proof_of_death():
    c = Clock()
    d = plan_teardown(ssh_alive=lambda: c.t > 300, api_state=lambda: None, pull=lambda: True,
                      **_kw(c))
    assert not d.delete


def test_provider_says_stopped_deletes_now():
    c = Clock()
    d = plan_teardown(ssh_alive=lambda: False, api_state=lambda: "Stopped", pull=lambda: True,
                      **_kw(c))
    assert d.delete and d.vm_state == "dead" and c.t == 0


def test_finished_copies_before_delete():
    c = Clock()
    order = []
    d = plan_teardown(ssh_alive=lambda: True, api_state=lambda: "running",
                      pull=lambda: order.append("pull") or True, finished=True, **_kw(c))
    assert d.delete and d.copied and order == ["pull"]


def test_finished_copy_fails_still_deletes_after_grace():
    # plan done (even early) but the final copy keeps failing: retry for the grace window, then
    # DELETE so billing stops; the per-block pulls already saved the results
    c = Clock()
    d = plan_teardown(ssh_alive=lambda: True, api_state=lambda: "running", pull=lambda: False,
                      finished=True, finish_grace_s=600, **_kw(c))
    assert d.delete and d.forced and not d.copied
    assert 600 <= c.t < 800


def test_finished_copy_recovers_within_grace():
    c = Clock()
    tries = iter([False] * 4 + [True])
    d = plan_teardown(ssh_alive=lambda: True, api_state=lambda: "running",
                      pull=lambda: next(tries), finished=True, **_kw(c))
    assert d.delete and d.copied and not d.forced


def test_money_rule_forces_delete_even_without_copy():
    c = Clock()
    d = plan_teardown(ssh_alive=lambda: False, api_state=lambda: "running", pull=lambda: False,
                      balance_low=True, **_kw(c))
    assert d.delete and d.forced and c.t < 60
    d2 = plan_teardown(ssh_alive=lambda: True, api_state=lambda: "running", pull=lambda: True,
                       hard_deadline_hit=True, **_kw(c))
    assert d2.delete and d2.copied and not d2.forced
