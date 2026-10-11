"""Regression tests for static verification of "A calls/imports/returns B" claims."""
import json

import pytest

from app.db.models import Project
from app.services.investigation.controller import Investigation
from app.services.llm.fake import FakeProvider
from app.services.rag.answer import AnswerRequest, run_baseline
from app.services.rag.relations import (
    INFERRED,
    UNSUPPORTED,
    UNVERIFIED,
    VERIFIED,
    extract_claims,
    verify_relationships,
)
from tests.chat_helpers import ask, use_fake
from tests.search_helpers import db_rows, import_repo, index

ROUTES = '''from app.images import save_product_image
from app.service import AuthService


def create_product(payload, session):
    product = {"name": payload["name"]}
    session.add(product)
    session.commit()
    return product


def upload_product_image(product_id, data, session):
    path = save_product_image(product_id, data)
    session.commit()
    return {"image_path": path}


def login(payload, session):
    user = AuthService(session).authenticate(payload["user"], payload["password"])
    return issue_token(user)


def issue_token(user):
    return f"token-{user}"
'''

IMAGES = '''def save_product_image(product_id, data):
    return f"product-{product_id}.png"
'''

SERVICE = '''class AuthService:
    def __init__(self, session):
        self.session = session

    def authenticate(self, user, password):
        return self._check(user, password)

    def _check(self, user, password):
        return user


class UserStore:
    def authenticate(self, user, password):
        return None
'''

DYNAMIC = '''import atexit


def notify_all(listeners, event):
    for listener in listeners:
        listener.notify(event)


def dispatch(handler, event):
    return handler(event)


def call_by_name(obj, name):
    return getattr(obj, name)()


def cleanup():
    pass


def register():
    atexit.register(cleanup)


class EmailListener:
    def notify(self, event):
        return event
'''

TASKS_TS = '''import { TaskModel } from "./taskModel";

export class TaskService {
  constructor(private store: { save(t: object): void }) {}

  createTask(title: string) {
    const task = TaskModel.create({ title });
    this.store.save(task);
    return task;
  }

  runHook(hook: Function) {
    return hook.call(this);
  }
}
'''

MODEL_TS = '''export const TaskModel = {
  create(fields: object) {
    return { ...fields };
  },
};
'''

REPO = {
    "rel/app/routes.py": ROUTES,
    "rel/app/images.py": IMAGES,
    "rel/app/service.py": SERVICE,
    "rel/app/dynamic.py": DYNAMIC,
    "rel/web/taskService.ts": TASKS_TS,
    "rel/web/taskModel.ts": MODEL_TS,
}


@pytest.fixture
def project(api):
    project = import_repo(api, REPO, name="rel")
    index(api, project["id"])
    return project


def check(api, settings, project_id, text):
    with api.app.state.session_factory() as session:
        return verify_relationships(session, settings, project_id, text)


def verdicts(report):
    return [(c.subject, c.relation, c.target, c.verdict) for c in report.checks]


def test_claim_extraction_handles_common_phrasings():
    _, claims = extract_claims(
        "The `create_product` function also calls the `save_product_image` function [E5]. "
        "`issue_token` is called by `login`. `login` returns the result of `issue_token`. "
        "`app/routes.py` imports `AuthService`. This function calls the database."
    )
    assert [(c.subject, c.relation, c.target) for c in claims] == [
        ("create_product", "calls", "save_product_image"),
        ("login", "calls", "issue_token"),
        ("login", "returns", "issue_token"),
        ("app/routes.py", "imports", "AuthService"),
    ]  # plain-English subjects ("This function") are not code names and are not checked


def test_false_direct_call_is_labelled_not_deleted(api, settings, project):
    """The recorded benchmark failure: create_product never calls save_product_image."""
    text = ("`create_product` stores the product [E1]. The `create_product` function also calls "
            "the `save_product_image` function [E2] to save the image. The endpoint returns "
            "the product [E1].")
    report = check(api, settings, project["id"], text)
    assert verdicts(report) == [("create_product", "calls", "save_product_image", UNSUPPORTED)]
    assert "**Unsupported claim:** The `create_product` function also calls" in report.text
    assert "no call to `save_product_image` appears in `create_product`" in report.text
    assert "(app/routes.py:5-9)" in report.text
    # Every original sentence is still there; only labels were added.
    for sentence in text.split(". "):
        assert sentence.rstrip(".") in report.text
    assert report.warnings and "create_product" in report.warnings[0]


def test_valid_direct_call_is_verified_and_left_unchanged(api, settings, project):
    text = "`upload_product_image` calls `save_product_image` to store the file [E1]."
    report = check(api, settings, project["id"], text)
    assert verdicts(report) == [("upload_product_image", "calls", "save_product_image",
                                 VERIFIED)]
    assert report.text == text and report.warnings == []


def test_cross_file_calls_resolve_constructed_receivers(api, settings, project):
    report = check(api, settings, project["id"],
                   "`login` calls `AuthService.authenticate` [E1]. "
                   "`login` calls `UserStore.authenticate` [E1]. "
                   "`AuthService.authenticate` calls `AuthService._check`.")
    assert verdicts(report) == [
        ("login", "calls", "AuthService.authenticate", VERIFIED),  # AuthService(session).x()
        ("login", "calls", "UserStore.authenticate", UNSUPPORTED),  # receiver is AuthService
        ("AuthService.authenticate", "calls", "AuthService._check", VERIFIED),  # self._check
    ]


def test_dynamic_dispatch_is_never_reported_as_certain(api, settings, project):
    report = check(api, settings, project["id"],
                   "`notify_all` calls `EmailListener.notify`. "
                   "`dispatch` calls `cleanup`. "
                   "`call_by_name` calls `cleanup`. "
                   "`register` calls `cleanup`.")
    by_target = {(c.subject, c.target): c for c in report.checks}
    notify = by_target[("notify_all", "EmailListener.notify")]
    assert notify.verdict == INFERRED and "dynamic dispatch" in notify.reason
    assert by_target[("dispatch", "cleanup")].verdict == UNVERIFIED  # calls its parameter
    assert "parameter `handler`" in by_target[("dispatch", "cleanup")].reason
    assert by_target[("call_by_name", "cleanup")].verdict == UNVERIFIED  # getattr(...)()
    register = by_target[("register", "cleanup")]
    assert register.verdict == INFERRED and "without calling it directly" in register.reason
    # Uncertain claims are qualified, not labelled as false and not removed.
    assert "**Unsupported claim:**" not in report.text
    assert report.text.count("(Inferred, not statically verified:") == 2
    assert report.text.count("(Not statically verified:") == 2


def test_typescript_calls_returns_and_dispatch(api, settings, project):
    report = check(api, settings, project["id"],
                   "`createTask` calls `TaskModel.create`. `createTask` returns `task`. "
                   "`createTask` calls `save`. `createTask` calls `deleteTask`. "
                   "`runHook` calls `createTask`.")
    assert verdicts(report) == [
        ("createTask", "calls", "TaskModel.create", VERIFIED),
        ("createTask", "returns", "task", VERIFIED),
        ("createTask", "calls", "save", VERIFIED),  # this.store.save(): method name matches
        ("createTask", "calls", "deleteTask", UNSUPPORTED),
        ("runHook", "calls", "createTask", UNVERIFIED),  # hook.call(this) is dynamic
    ]


def test_imports_and_returns(api, settings, project):
    report = check(api, settings, project["id"],
                   "`app/routes.py` imports `AuthService`. `app/routes.py` imports `requests`. "
                   "`upload_product_image` imports `save_product_image`. "
                   "`login` returns `issue_token`. `login` returns `UserStore`. "
                   "`cleanup` returns `register`.")
    assert verdicts(report) == [
        ("app/routes.py", "imports", "AuthService", VERIFIED),
        ("app/routes.py", "imports", "requests", UNSUPPORTED),
        ("upload_product_image", "imports", "save_product_image", VERIFIED),
        ("login", "returns", "issue_token", VERIFIED),
        ("login", "returns", "UserStore", UNSUPPORTED),
        ("cleanup", "returns", "register", UNSUPPORTED),  # no return value at all
    ]


def test_unresolvable_subjects_are_qualified_not_judged(api, settings, project):
    text = "`router` calls `login` for POST requests. `requests.get` calls `urlopen`."
    report = check(api, settings, project["id"], text)
    assert {c.verdict for c in report.checks} == {UNVERIFIED}
    assert "not a symbol of this project" in report.checks[0].reason
    assert "**Unsupported claim:**" not in report.text and report.warnings == []
    assert report.text.startswith("`router` calls `login` for POST requests.")


def test_answers_without_relationship_claims_are_untouched(api, settings, project):
    text = ("The upload endpoint stores the image and commits the session [E1]. "
            "Inferred: images are served from the upload directory.")
    report = check(api, settings, project["id"], text)
    assert report.checks == [] and report.text == text


def test_pipeline_labels_false_claims_and_persists_the_checks(api, settings, project):
    script = ("`upload_product_image` calls `save_product_image` [E1]. "
              "`create_product` calls `save_product_image` [E1].")
    use_fake(api, FakeProvider(script=[script]))
    message = ask(api, project["id"], "How are product images saved?", mode="baseline")["message"]
    assert "**Unsupported claim:** `create_product` calls `save_product_image`" in \
        message["content"]
    assert message["content"].startswith("`upload_product_image` calls `save_product_image`")
    assert any("Unsupported relationship claim" in w for w in message["warnings"])
    steps = [s for s in message["run"]["trace"] if s.get("step") == "relationship_check"]
    assert [c["verdict"] for c in steps[0]["checks"]] == [VERIFIED, UNSUPPORTED]
    (trace,) = db_rows(settings, "SELECT trace FROM investigation_runs")[0]
    assert "relationship_check" in json.loads(trace)[-1]["step"]


def test_baseline_result_carries_relationship_checks(api, settings, project):
    with api.app.state.session_factory() as session:
        req = AnswerRequest(
            session=session, settings=settings,
            provider=FakeProvider(script=["`login` calls `AuthService.authenticate` [E1]."]),
            project=session.get(Project, project["id"]), question="How does login work?")
        result = run_baseline(req)
    assert [r["verdict"] for r in result.relationships] == [VERIFIED]
    assert result.text == result.raw_text  # verified claims are not annotated


def test_planner_prefers_calls_over_imports_and_says_why(api, settings, project):
    with api.app.state.session_factory() as session:
        req = AnswerRequest(session=session, settings=settings, provider=FakeProvider(),
                            project=session.get(Project, project["id"]),
                            question="How does sign-in work?")
        inv = Investigation(req, [])
        # login (calls AuthService) and the module's import block, as in the recorded failure.
        inv.evidence.add_range("app/routes.py", 18, 20, methods=["test"], provenance="seed")
        inv.evidence.add_range("app/routes.py", 1, 2, methods=["test"], provenance="seed")
        targets = inv.deterministic_targets(limit=5)
        names = [t["arguments"]["name"] for t in targets]
        # AuthService is both called (E1) and imported (E2): the call reason is kept.
        auth = next(t for t in targets if t["arguments"]["name"] == "AuthService")
        assert auth["reason"].startswith("called in E1")
        imported_only = next(t for t in targets
                             if t["arguments"]["name"] == "save_product_image")
        assert imported_only["reason"].startswith("imported in E2")
        assert "not necessarily called" in imported_only["reason"]
        assert names.index("AuthService") < names.index("save_product_image")
        inv.run_tool("find_symbol", {"name": "save_product_image"}, "deterministic",
                     imported_only["reason"])
        fetched = inv.evidence.items[-1]
        assert fetched.path == "app/images.py"
        assert "not necessarily called" in fetched.provenance
