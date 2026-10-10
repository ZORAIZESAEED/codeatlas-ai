import pytest

from app.services.retrieval.fusion import reciprocal_rank_fusion
from app.services.retrieval.keyword import fts_match_expression
from app.services.retrieval.models import Hit
from app.services.retrieval.terms import identifier_candidates, query_terms, split_identifier
from tests.search_helpers import import_repo, index, search


@pytest.fixture
def project(api):
    project = import_repo(api)
    index(api, project["id"])
    return project


def top(response, n=1):
    return [(r["file_path"], r["symbol_name"]) for r in response["results"][:n]]


# --- terms -----------------------------------------------------------------------------------

def test_identifier_splitting():
    assert split_identifier("createAccessToken") == ["create", "access", "token"]
    assert split_identifier("create_access_token") == ["create", "access", "token"]
    assert split_identifier("HTTPServer2") == ["http", "server", "2"]
    assert split_identifier("token") == []


def test_query_terms_keep_identifiers_and_drop_stopwords():
    terms = query_terms("Where is createAccessToken handled?")
    assert terms[0] == "createaccesstoken"
    assert {"create", "access", "token"} <= set(terms)
    assert "where" not in terms and "handled" not in terms
    assert identifier_candidates("open core/database.py and AuthService") == [
        "open", "core/database.py", "AuthService",
    ]


@pytest.mark.parametrize(
    "query",
    ['" OR 1=1 --', "NEAR(a b)", "auth* -token", "a AND (b", "x'; DROP TABLE projects; --",
     "{path}: secret", "^start", "foo:bar", "élève naïve", "\"\"\""],
)
def test_malformed_queries_are_safe(api, project, query):
    for mode in ("keyword", "hybrid"):
        response = api.post(
            f"/api/projects/{project['id']}/search", json={"query": query, "mode": mode}
        )
        assert response.status_code == 200, response.text
    assert api.get("/api/projects").status_code == 200  # nothing was dropped


def test_fts_expression_quotes_every_term():
    expression = fts_match_expression("p-1", 'auth" OR x NEAR(y')
    assert expression.count('"') % 2 == 0
    assert '"auth"' in expression and '"near"' in expression
    assert fts_match_expression("p-1", "   ?? ") is None


# --- keyword search --------------------------------------------------------------------------

def test_exact_symbol_lookup(api, project):
    response = search(api, project["id"], "AuthService", mode="keyword")
    first = response["results"][0]
    assert (first["file_path"], first["symbol_name"]) == ("app/auth.py", "AuthService")
    assert first["retrieval_methods"][0] == "exact"
    assert first["symbol_type"] == "class"
    assert "class AuthService" in first["snippet"]


def test_snake_case_identifier(api, project):
    response = search(api, project["id"], "create_access_token", mode="keyword")
    assert top(response) == [("app/auth.py", "create_access_token")]


def test_camel_case_identifier_and_parts(api, project):
    response = search(api, project["id"], "createAccessToken", mode="keyword")
    assert top(response) == [("web/api.ts", "createAccessToken")]
    # Sub-words find both the camelCase and the snake_case implementations.
    parts = search(api, project["id"], "access token", mode="keyword")
    symbols = {r["symbol_name"] for r in parts["results"][:3]}
    assert {"createAccessToken", "create_access_token"} <= symbols


def test_file_path_search(api, project):
    response = search(api, project["id"], "database.py", mode="keyword")
    assert response["results"][0]["file_path"] == "app/database.py"
    assert "exact" in response["results"][0]["retrieval_methods"]
    response = search(api, project["id"], "web/validate.js", mode="keyword")
    assert response["results"][0]["file_path"] == "web/validate.js"


def test_comments_and_docs_are_searchable(api, project):
    response = search(api, project["id"], "connection pool", mode="keyword")
    assert top(response) == [("app/database.py", "init_db")]
    readme = search(api, project["id"], "tiny service", mode="keyword")
    assert "README.md" in [r["file_path"] for r in readme["results"][:2]]


def test_no_results(api, project):
    response = search(api, project["id"], "kubernetes helm chart", mode="keyword")
    assert response["results"] == [] and response["citations"] == []


# --- fusion / hybrid -------------------------------------------------------------------------

def test_rrf_fusion_and_dedup():
    lists = {
        "keyword": [Hit(1, 1, -5.0), Hit(2, 2, -3.0), Hit(1, 3, -1.0)],
        "semantic": [Hit(2, 1, 0.1), Hit(3, 2, 0.2)],
    }
    fused = reciprocal_rank_fusion(lists, {"keyword": 1.0, "semantic": 1.0}, k=60)
    ids = [f[0] for f in fused]
    assert ids == [2, 1, 3]  # in both lists beats a single first place
    assert len(ids) == len(set(ids))
    two = dict((f[0], f) for f in fused)[2]
    assert two[1] == pytest.approx(1 / 62 + 1 / 61)
    assert two[2] == {"keyword": 2, "semantic": 1}
    assert two[3] == {"keyword": -3.0, "semantic": 0.1}
    weighted = reciprocal_rank_fusion(lists, {"keyword": 0.0, "semantic": 1.0}, k=60)
    assert weighted[0][0] == 2 and weighted[1][0] == 3


def test_rrf_ties_are_stable():
    lists = {
        "keyword": [Hit(7, 1, 0.0), Hit(3, 2, 0.0)],
        "semantic": [Hit(3, 1, 0.0), Hit(7, 2, 0.0)],
    }
    first = reciprocal_rank_fusion(lists, {}, k=60)
    assert [f[0] for f in first] == [3, 7]  # equal scores: lower entry id first
    assert reciprocal_rank_fusion(lists, {}, k=60) == first


def test_exact_match_ignores_plain_words_with_other_case(api):
    project = import_repo(
        api,
        {
            "s/models.py": "class User:\n    name = ''\n",
            "s/views.py": "def login():\n    return 'user signs in'\n",
        },
        name="case",
    )
    index(api, project["id"])
    results = search(api, project["id"], "where does a user log in", mode="keyword")["results"]
    assert all("exact" not in r["retrieval_methods"] for r in results)  # "user" is not `User`
    named = search(api, project["id"], "login", mode="keyword")["results"][0]
    assert named["symbol_name"] == "login" and named["retrieval_methods"][0] == "exact"
    assert search(api, project["id"], "User", mode="keyword")["results"][0]["symbol_name"] == "User"


def test_fusion_weights_depend_on_query_shape(api, project, settings):
    from app.services.retrieval.hybrid import fusion_weights

    assert fusion_weights(settings, "create_access_token")["keyword"] == 1.0
    assert fusion_weights(settings, "where is AuthService used")["keyword"] == 1.0
    assert fusion_weights(settings, "core/db.py")["keyword"] == 1.0
    assert fusion_weights(settings, "where are passwords hashed")["keyword"] == 0.25
    response = search(api, project["id"], "where are passwords hashed", mode="hybrid")
    assert response["fusion_weights"] == {"exact": 1.0, "keyword": 0.25, "semantic": 1.0}
    assert search(api, project["id"], "x", mode="semantic")["fusion_weights"] == {"semantic": 1.0}


def test_hybrid_combines_methods(api, project):
    response = search(api, project["id"], "create_access_token", mode="hybrid")
    assert response["mode_used"] == "hybrid"
    first = response["results"][0]
    assert first["symbol_name"] == "create_access_token"
    assert set(first["retrieval_methods"]) == {"exact", "keyword", "semantic"}
    assert set(first["method_ranks"]) == {"exact", "keyword", "semantic"}
    assert "keyword" in first["raw_scores"] and "semantic" in first["raw_scores"]
    chunk_ids = [r["chunk_id"] for r in response["results"]]
    assert len(chunk_ids) == len(set(chunk_ids))
    assert [r["rank"] for r in response["results"]] == list(range(1, len(chunk_ids) + 1))
    assert response["candidate_counts"]["semantic"] > 0
    assert "total" in response["timings_ms"]


def test_results_are_deterministic(api, project):
    runs = [search(api, project["id"], "login error", mode="hybrid") for _ in range(3)]
    orders = [[r["chunk_id"] for r in run["results"]] for run in runs]
    assert orders[0] == orders[1] == orders[2]


def test_semantic_mode_only_uses_vectors(api, project):
    response = search(api, project["id"], "jwt access token", mode="semantic", limit=3)
    assert response["mode_used"] == "semantic"
    assert all(r["retrieval_methods"] == ["semantic"] for r in response["results"])
    assert len(response["results"]) == 3


def test_rerank_toggle(api, project):
    on = search(api, project["id"], "AuthService authenticate", mode="hybrid", rerank=True)
    off = search(api, project["id"], "AuthService authenticate", mode="hybrid", rerank=False)
    default = search(api, project["id"], "AuthService authenticate", mode="hybrid")
    assert on["reranked"] and not off["reranked"]
    assert default["reranked"] is False  # opt-in: it did not improve the benchmark
    assert on["results"][0]["rerank_features"]["exact_symbol"] == 1.0
    assert off["results"][0]["rerank_features"] == {}
    assert off["results"][0]["score"] == off["results"][0]["fusion_score"]


@pytest.mark.parametrize(
    "filters, expected_paths",
    [
        ({"language": "TypeScript"}, {"web/api.ts"}),
        ({"file_path": "app/auth.py"}, {"app/auth.py"}),
        ({"directory": "web"}, {"web/api.ts", "web/validate.js"}),
        ({"directory": "./web/"}, {"web/api.ts", "web/validate.js"}),
    ],
)
def test_filters(api, project, filters, expected_paths):
    for mode in ("keyword", "semantic", "hybrid"):
        response = search(api, project["id"], "token user login request", mode=mode, **filters)
        paths = {r["file_path"] for r in response["results"]}
        assert paths and paths <= expected_paths, (mode, paths)


def test_symbol_type_filter(api, project):
    for mode in ("keyword", "semantic", "hybrid"):
        response = search(api, project["id"], "token login", mode=mode, symbol_type="class")
        kinds = {r["symbol_type"] for r in response["results"]}
        assert kinds == {"class"}, (mode, kinds)


def test_filters_never_match_outside_manifest(api, project):
    # Wildcards are escaped; traversal is rejected before reaching the database.
    response = search(api, project["id"], "token", mode="hybrid", directory="%")
    assert response["results"] == []
    for bad in ({"directory": "../.."}, {"file_path": "web/../../etc/passwd"}):
        response = api.post(
            f"/api/projects/{project['id']}/search", json={"query": "token", **bad}
        )
        assert response.status_code == 422


def test_limit_and_validation(api, project):
    pid = project["id"]
    assert len(search(api, pid, "token", limit=2)["results"]) <= 2
    for body in (
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 501},
        {"query": "token", "limit": 0},
        {"query": "token", "limit": 51},
        {"query": "token", "mode": "magic"},
    ):
        response = api.post(f"/api/projects/{pid}/search", json=body)
        assert response.status_code == 422, body


def test_results_have_verified_citations(api, project, settings):
    response = search(api, project["id"], "authenticate user password", mode="hybrid")
    assert response["citations"]
    for result, citation in zip(response["results"], response["citations"]):
        assert citation["reference"] == (
            f"{result['file_path']}:{result['start_line']}-{result['end_line']}"
        )
        source = api.get(
            f"/api/projects/{project['id']}/files/content", params={"path": result["file_path"]}
        ).json()["content"]
        lines = source.split("\n")
        expected = "\n".join(lines[result["start_line"] - 1 : result["end_line"]])
        assert expected.startswith(result["snippet"].rstrip("\n")[:200])


def test_snippets_are_bounded(api, settings):
    big = "def huge():\n" + "".join(f"    value_{i} = {i}\n" for i in range(90))
    project = import_repo(api, {"b/big.py": big}, name="big")
    index(api, project["id"])
    api.app.state.settings = settings.model_copy(update={"search_snippet_max_lines": 5})
    result = search(api, project["id"], "huge", mode="keyword")["results"][0]
    assert result["snippet_truncated"] is True
    assert result["snippet"].count("\n") == 5
    assert result["end_line"] - result["start_line"] + 1 == 91


# --- API states ------------------------------------------------------------------------------

def test_search_unknown_project(api):
    response = api.post("/api/projects/nope/search", json={"query": "token"})
    assert response.status_code == 404


def test_search_requires_index(api):
    project = import_repo(api)
    response = api.post(f"/api/projects/{project['id']}/search", json={"query": "token"})
    assert response.status_code == 409
    assert "not been indexed" in response.json()["detail"]


def test_stale_index_still_searches_with_warning(api, project):
    api.post(f"/api/projects/{project['id']}/analyze")
    response = search(api, project["id"], "AuthService", mode="hybrid")
    assert response["index_status"]["status"] == "stale"
    assert any("stale" in w for w in response["warnings"])
    assert response["results"]


def test_semantic_unavailable(api, project, monkeypatch):
    from app.services.embeddings.base import EmbeddingError
    from app.services.embeddings.fake_provider import FakeEmbeddingProvider

    def down(self, text):
        raise EmbeddingError("Could not load embedding model 'fake'.")

    monkeypatch.setattr(FakeEmbeddingProvider, "_embed_query", down)
    response = api.post(
        f"/api/projects/{project['id']}/search", json={"query": "token", "mode": "semantic"}
    )
    assert response.status_code == 503
    assert "Semantic search is unavailable" in response.json()["detail"]

    hybrid = search(api, project["id"], "create_access_token", mode="hybrid")
    assert hybrid["mode_requested"] == "hybrid"
    assert hybrid["mode_used"] == "keyword"  # explicitly labelled fallback
    assert "Could not load" in hybrid["fallback_reason"]
    assert hybrid["warnings"]
    assert all("semantic" not in r["retrieval_methods"] for r in hybrid["results"])
    assert hybrid["results"][0]["symbol_name"] == "create_access_token"


def test_missing_vector_collection_is_reported(api, project, settings):
    from app.services.indexing import vector_store

    vector_store.delete_collections(settings, project["id"])
    response = api.post(
        f"/api/projects/{project['id']}/search", json={"query": "token", "mode": "semantic"}
    )
    assert response.status_code == 503
    assert "reindex" in response.json()["detail"]


def test_tampered_source_is_not_cited(api, project, settings):
    from tests.search_helpers import workspace_file, write_lf

    path = workspace_file(settings, project["id"], "app/database.py")
    write_lf(path, path.read_text().replace("connection pool", "CONNECTION POOL"))
    response = search(api, project["id"], "init_db", mode="keyword")
    # The edited chunk no longer matches its indexed hash; untouched chunks remain citable.
    assert all(r["symbol_name"] != "init_db" for r in response["results"])
    assert response["candidate_counts"].get("invalid_source", 0) >= 1
    assert any("no longer match" in w for w in response["warnings"])
