from wattnot.e2e.live import other_values_unchanged, pr_body

F = {"maxNumSeqs": 128}
OLD = {"a": 1, "selfHosted": {"maxNumSeqs": 64, "receiptSha256": "old"}}


def test_receipt_hash_change_is_allowed():
    new = {"a": 1, "selfHosted": {"maxNumSeqs": 128, "receiptSha256": "new"}}
    assert other_values_unchanged(OLD, new, "selfHosted", F)


def test_other_changes_still_fail():
    assert not other_values_unchanged(OLD, {"a": 2, "selfHosted": {"maxNumSeqs": 128, "receiptSha256": "n"}}, "selfHosted", F)
    assert not other_values_unchanged(OLD, {"a": 1, "selfHosted": {"maxNumSeqs": 128, "receiptSha256": "n", "x": 1}}, "selfHosted", F)
    assert not other_values_unchanged({"a": 1}, {"a": 1, "receiptSha256": "n"}, "selfHosted", F)


def test_new_block_allowed():
    assert other_values_unchanged({"a": 1}, {"a": 1, "selfHosted": {"maxNumSeqs": 128, "receiptSha256": "n"}}, "selfHosted", F)


def test_pr_body_names_approver():
    b = pr_body({"body": "x"}, "abc", {"who": "Akash K Raju (delegated reviewer)", "decision": "approved", "why": None})
    assert "approved by Akash K Raju (delegated reviewer)." in b and "None" not in b
