"""Tests for the generated /docs pages (API reference and mise tasks)."""

import os

import pytest

from resources.daemon.constants import SCRIPT_DIR
from resources.daemon.docs_ui import DOC_PAGES, generated_doc_markdown
from resources.docs_generated import render_api_endpoints
from tests.test_handler import _get_response_bytes, _make_handler

SPEC = """
openapi: "3.1.0"
info: {title: T, description: Hello API}
security:
  - apiKey: []
paths:
  /health:
    get:
      summary: Health check
      security: [{}, {apiKey: []}]
      tags: [monitoring]
      parameters:
        - {name: verbose, in: query, schema: {type: boolean}, description: More detail}
      responses:
        "200": {description: OK}
  /jobs:
    post:
      summary: Submit
      tags: [jobs]
      requestBody:
        content:
          application/json:
            schema: {$ref: "#/components/schemas/JobSubmit"}
      responses:
        "202": {description: Accepted}
  /open:
    get:
      security: []
      responses: {}
"""


def test_api_endpoints_grouped_with_details():
  md = render_api_endpoints(SPEC)
  assert "## monitoring" in md and "## jobs" in md and "## other" in md
  assert "### GET /health" in md
  assert "Auth: optional (API key or bearer token)" in md
  assert "Auth: API key or bearer token" in md
  assert "Auth: none" in md
  assert "| `verbose` | query | boolean | no | More detail |" in md
  assert "`application/json` (JobSubmit)" in md
  assert "| 202 | Accepted |" in md


def test_pages_registered():
  slugs = [s for s, _ in DOC_PAGES]
  assert "api-reference" in slugs and "mise-tasks" in slugs


def test_generated_markdown_missing_sources(tmp_path):
  assert "Not available in this build" in generated_doc_markdown("api-reference", tmp_path)
  assert "Not available in this build" in generated_doc_markdown("mise-tasks", tmp_path)


def test_generated_markdown_bad_spec(tmp_path):
  (tmp_path / "docs").mkdir()
  (tmp_path / "docs" / "openapi.yaml").write_text("a: [unclosed", encoding="utf-8")
  assert "Not available in this build" in generated_doc_markdown("api-reference", tmp_path)


def test_generated_markdown_mise_tasks(tmp_path):
  task = tmp_path / ".mise" / "tasks" / "test" / "run"
  task.parent.mkdir(parents=True)
  task.write_text('#!/bin/sh\n#MISE description="Run tests"\n', encoding="utf-8")
  md = generated_doc_markdown("mise-tasks", tmp_path)
  assert "<!--" not in md and md.lstrip().startswith("# Mise Tasks")
  assert "`mise run test:run`" in md


@pytest.mark.parametrize("slug,needle", [("api-reference", "GET /health"), ("mise-tasks", "mise run")])
def test_docs_routes_render_real_sources(slug, needle):
  assert os.path.isfile(os.path.join(SCRIPT_DIR, "docs", "openapi.yaml"))
  h = _make_handler()
  h.server.version = "1.0"
  h._get_docs("/docs/" + slug, {})
  assert h._response_code == 200
  assert needle in _get_response_bytes(h).decode()


def test_docs_route_graceful_when_source_missing(monkeypatch):
  monkeypatch.setattr("resources.daemon.handler.generated_doc_markdown", lambda slug: "# X\n\nNot available in this build\n")
  h = _make_handler()
  h.server.version = "1.0"
  h._get_docs("/docs/mise-tasks", {})
  assert h._response_code == 200
  assert "Not available in this build" in _get_response_bytes(h).decode()
