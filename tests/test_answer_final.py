from wattnot.node.answercheck import AnswerCheck, final_answer, same_answer


def test_final_answer_forms():
    assert final_answer("so 8,000 x 0.012 = $96\n\n#### 125") == "125"
    assert final_answer("The answer is \\boxed{1,250.00}") == "1250"
    assert final_answer("Total is $1,423.") == "1423"
    assert final_answer("Money left =") == ""


def test_same_answer_strict_on_empty_and_truncated():
    assert same_answer("steps... #### 26", "other steps #### 26.0")
    assert not same_answer("", "")
    assert not same_answer("Money left = 1500 - 1423\nMoney left =", "Money left =")
    assert not same_answer("#### 26", "#### 27")


def test_stock_vs_stock_passes_with_identical_answers():
    class G:
        e = 0
        def nvmlDeviceGetTotalEnergyConsumption(self, _):
            G.e += 1000
            return G.e
    answers = iter(["#### 1", "#### 2"] * 2)
    chk = AnswerCheck(lambda p: next(answers), G(), prompts=["a", "b"])
    chk.stock()
    assert chk.at()["answers_same"] == "2/2"
