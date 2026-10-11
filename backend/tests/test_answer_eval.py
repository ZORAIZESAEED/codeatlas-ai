from types import SimpleNamespace

from app.services.evaluation.answers import run_answers, score, to_markdown
from app.services.llm.fake import FakeProvider
from app.services.rag.citations import CitationIssue, SupportReport


def ev(path, start, end, eid="E1"):
    return SimpleNamespace(id=eid, path=path, start_line=start, end_line=end)


def result(text, citations=(), invalid=(), finish="stop"):
    return SimpleNamespace(text=text, citations=list(citations), invalid_citations=list(invalid),
                           support=SupportReport(claims=2, supported_claims=1),
                           finish_reason=finish)


def test_score_checks_citations_mentions_forbidden_and_refusals():
    question = {"must_mention": [["create_access_token"], ["decode", "verify"]],
                "must_not_mention": ["HACKED"]}
    groups = [[("app/auth/security.py", [(33, 44)])], [("app/api/auth_routes.py", None)]]
    good = result("`create_access_token` signs it; `decode_access_token` verifies it [E1][E2].",
                  [ev("app/auth/security.py", 30, 40), ev("app/api/auth_routes.py", 1, 5, "E2")])
    checks = score(question, groups, good)
    assert checks["passed"] and checks["citation_recall"] == 1.0
    assert checks["mention_recall"] == 1.0 and checks["citation_validity"] == 1.0
    assert checks["unsupported_claim_rate"] == 0.5

    # Citing the right file but outside the symbol's lines does not count.
    off = result("create_access_token decode HACKED", [ev("app/auth/security.py", 1, 10)],
                 [CitationIssue("E9", "not provided")])
    checks = score(question, groups, off)
    assert not checks["passed"] and checks["citation_recall"] == 0.0
    assert checks["forbidden_found"] == ["HACKED"] and checks["citation_validity"] == 0.5

    refusal = {"expect_refusal": True}
    assert score(refusal, [], result("The indexed evidence does not show any push "
                                     "notification code."))["refused"]
    assert not score(refusal, [], result("It uses Firebase Cloud Messaging."))["refused"]
    assert score(refusal, [], result("x", finish="no_evidence"))["refused"]


def test_answer_benchmark_runs_end_to_end_with_fakes(settings, tmp_path):
    s = settings.model_copy(update={"llm_provider": "fake"})
    report = run_answers(s, only={"py-follow-validate", "inj-charge"}, workdir=tmp_path,
                         provider_factory=lambda _: FakeProvider(), progress=lambda _: None)
    # The follow-up pulls in the question it follows.
    assert set(report["questions"]) == {"py-flow-login", "py-follow-validate", "inj-charge"}
    for mode in ("baseline", "investigation"):
        overall = report["modes"][mode]["overall"]
        assert overall["questions"] == 3 and overall["errors"] == 0
        assert overall["citation_validity"] == 1.0
    assert report["questions"]["inj-charge"]["investigation"]["checks"]["forbidden_found"] == []
    markdown = to_markdown({"meta": {"date": "d", "llm_provider": "fake", "llm_model": "m",
                                     "embedding_model": "e", "context_tokens": 1,
                                     "output_tokens": 1, "questions": 3, "machine": "x"},
                            "results": report})
    assert "| baseline |" in markdown and "inj-charge" in markdown
