"""Provider/model selection, manifest threading, fallback, and escalation.

Covers scripts/resolve_provider_model.py (per-host selection, override
precedence, halt-and-ask, escalation), the structured `model` object threaded
into build_analysis_manifest.py, and a static guard that the retired default
model names do not reappear as active defaults anywhere in the shipped skill.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest

from helpers import run_script, ROOT

SKILL_DIR = os.path.join(ROOT, "git-worklog")
CONFIG_PATH = os.path.join(SKILL_DIR, "config", "provider_models.json")

NEW_DEFAULTS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-5.6-luna",
    "google": "gemini-3.5-flash",
}
EXPECTED_ESCALATION = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-5.6-terra",
    "google": "gemini-3.1-pro-preview",
}


def _write_config(body: dict) -> str:
    fd, path = tempfile.mkstemp(prefix="rw_cfg_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
    return path


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class TestProviderMapping(unittest.TestCase):
    """Each host selects exactly its own provider's default model."""

    def test_anthropic_selects_haiku(self):
        out, rc, err = run_script("resolve_provider_model.py", ["--host", "anthropic"])
        self.assertEqual(rc, 0, err)
        self.assertTrue(out["ok"])
        self.assertEqual(out["provider"], "anthropic")
        self.assertEqual(out["model"]["model_id"], "claude-haiku-4-5")
        self.assertEqual(out["model"]["display_name"], "Claude Haiku 4.5")
        # anthropic has no reasoning_effort — the key must be absent, not "".
        self.assertNotIn("reasoning_effort", out["model"])

    def test_openai_selects_luna_with_low_effort(self):
        out, rc, err = run_script("resolve_provider_model.py", ["--host", "openai"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out["provider"], "openai")
        self.assertEqual(out["model"]["model_id"], "gpt-5.6-luna")
        self.assertEqual(out["model"]["reasoning_effort"], "low")

    def test_google_selects_flash_without_effort(self):
        out, rc, err = run_script("resolve_provider_model.py", ["--host", "google"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out["provider"], "google")
        self.assertEqual(out["model"]["model_id"], "gemini-3.5-flash")
        self.assertNotIn("reasoning_effort", out["model"])

    def test_unknown_host_errors(self):
        out, rc, _ = run_script("resolve_provider_model.py", ["--host", "codex"])
        self.assertEqual(rc, 2)
        self.assertFalse(out["ok"])
        self.assertEqual(out["errors"][0]["code"], "UNKNOWN_HOST")
        self.assertNotIn("model", out)  # never falls through to a model

    def test_missing_host_errors_without_guessing(self):
        out, rc, _ = run_script("resolve_provider_model.py", [])
        self.assertEqual(rc, 2)
        self.assertEqual(out["errors"][0]["code"], "UNKNOWN_HOST")
        # It must NOT default to the first provider.
        self.assertNotIn("provider", out)

    def test_does_not_borrow_another_providers_model(self):
        out, _, _ = run_script("resolve_provider_model.py", ["--host", "anthropic"])
        blob = json.dumps(out)
        self.assertNotIn("gpt-5.6-luna", blob)
        self.assertNotIn("gemini-3.5-flash", blob)


class TestOverrides(unittest.TestCase):
    """cli --model > env REPO_WORKLOG_<HOST>_MODEL > config default."""

    def test_no_override_uses_config_default(self):
        out, _, _ = run_script("resolve_provider_model.py", ["--host", "openai"])
        self.assertEqual(out["model"]["model_id"], "gpt-5.6-luna")
        self.assertEqual(out["model_id_source"], "config")

    def test_env_override(self):
        out, _, _ = run_script(
            "resolve_provider_model.py", ["--host", "openai"],
            env={"REPO_WORKLOG_OPENAI_MODEL": "gpt-5.6-luna-2026-07"})
        self.assertEqual(out["model"]["model_id"], "gpt-5.6-luna-2026-07")
        self.assertEqual(out["model_id_source"], "env")

    def test_cli_beats_env(self):
        out, _, _ = run_script(
            "resolve_provider_model.py", ["--host", "openai", "--model", "gpt-from-cli"],
            env={"REPO_WORKLOG_OPENAI_MODEL": "gpt-from-env"})
        self.assertEqual(out["model"]["model_id"], "gpt-from-cli")
        self.assertEqual(out["model_id_source"], "cli")


class TestManifestModel(unittest.TestCase):
    """The resolved model object is threaded verbatim onto the manifest."""

    _HISTORY = json.dumps({"ok": True, "commits": []})

    def _manifest(self, provider, model_json=None):
        args = ["--date", "2026-07-15", "--timezone", "Asia/Taipei", "--provider", provider]
        if model_json is not None:
            args += ["--model-json", model_json]
        out, rc, err = run_script("build_analysis_manifest.py", args, stdin=self._HISTORY)
        self.assertEqual(rc, 0, err)
        self.assertTrue(out["ok"], err)
        return out

    def test_openai_manifest_has_low_reasoning_effort(self):
        resolved, _, _ = run_script("resolve_provider_model.py", ["--host", "openai"])
        man = self._manifest("openai", json.dumps(resolved["model"]))
        self.assertEqual(man["provider"], "openai")
        self.assertEqual(man["model"]["reasoning_effort"], "low")

    def test_anthropic_manifest_has_no_reasoning_effort(self):
        resolved, _, _ = run_script("resolve_provider_model.py", ["--host", "anthropic"])
        man = self._manifest("anthropic", json.dumps(resolved["model"]))
        self.assertNotIn("reasoning_effort", man["model"])

    def test_google_manifest_has_no_reasoning_effort(self):
        resolved, _, _ = run_script("resolve_provider_model.py", ["--host", "google"])
        man = self._manifest("google", json.dumps(resolved["model"]))
        self.assertNotIn("reasoning_effort", man["model"])

    def test_model_override_flows_into_manifest(self):
        override = {"display_name": "Custom", "model_id": "custom-xyz"}
        man = self._manifest("openai", json.dumps(override))
        self.assertEqual(man["model"], override)

    def test_no_model_json_yields_null(self):
        man = self._manifest("anthropic")
        self.assertIsNone(man["model"])

    def test_bad_model_json_is_rejected(self):
        out, rc, _ = run_script(
            "build_analysis_manifest.py",
            ["--date", "2026-07-15", "--timezone", "Asia/Taipei", "--model-json", "not-json"],
            stdin=self._HISTORY)
        self.assertEqual(rc, 2)
        self.assertEqual(out["errors"][0]["code"], "BAD_MODEL_JSON")


class TestFallback(unittest.TestCase):
    """An unresolvable model halts; no silent fallback to a default or pricier model."""

    def test_empty_model_id_halts_with_candidates(self):
        cfg = _write_config({
            "providers": {
                "openai": {"display_name": "GPT-5.6 Luna", "model_id": ""},
                "anthropic": {"display_name": "Claude Haiku 4.5", "model_id": "claude-haiku-4-5"},
                "google": {"display_name": "Gemini 3.5 Flash", "model_id": "gemini-3.5-flash"},
            },
            "model_unavailable_policy": "halt-and-ask",
        })
        try:
            out, rc, _ = run_script("resolve_provider_model.py",
                                    ["--host", "openai", "--config", cfg])
            self.assertEqual(rc, 2)
            err = out["errors"][0]
            self.assertEqual(err["code"], "MODEL_UNAVAILABLE")
            self.assertEqual(err["provider"], "openai")
            self.assertIn("requested_model_id", err)          # requested id reported
            self.assertTrue(err["candidates"])                # selectable list supplied
            self.assertNotIn("model", out)                    # no auto-fallback model
        finally:
            os.remove(cfg)

    def test_does_not_use_escalation_model_as_fallback(self):
        # Default resolve (no --escalate) must never surface the escalation model
        # as the active model, even though it is available.
        out, _, _ = run_script("resolve_provider_model.py", ["--host", "openai"])
        self.assertEqual(out["escalated"], False)
        self.assertEqual(out["model"]["model_id"], "gpt-5.6-luna")
        self.assertNotEqual(out["model"]["model_id"], out["escalation"]["model_id"])


class TestEscalation(unittest.TestCase):
    """Escalation is opt-in; automatic is off; a re-run mints a fresh preview id."""

    def test_policy_automatic_is_false(self):
        out, _, _ = run_script("resolve_provider_model.py", ["--host", "openai"])
        self.assertFalse(out["escalation_policy"]["automatic"])

    def test_confidence_unknown_only_suggests(self):
        # The resolver never escalates on its own; without --escalate the active
        # model stays the cost-first default regardless of subagent confidence.
        out, _, _ = run_script("resolve_provider_model.py", ["--host", "anthropic"])
        self.assertFalse(out["escalated"])
        self.assertEqual(out["model"]["model_id"], "claude-haiku-4-5")

    def test_escalate_flag_selects_escalation_model(self):
        out, rc, err = run_script("resolve_provider_model.py",
                                  ["--host", "openai", "--escalate"])
        self.assertEqual(rc, 0, err)
        self.assertTrue(out["escalated"])
        self.assertEqual(out["model"]["model_id"], "gpt-5.6-terra")
        self.assertEqual(out["model"]["reasoning_effort"], "medium")

    def test_escalation_unavailable_errors(self):
        cfg = _write_config({
            "providers": {"google": {"display_name": "Gemini 3.5 Flash",
                                     "model_id": "gemini-3.5-flash"}},
            "escalation_policy": {"automatic": False, "suggest_when": []},
        })
        try:
            out, rc, _ = run_script("resolve_provider_model.py",
                                    ["--host", "google", "--escalate", "--config", cfg])
            self.assertEqual(rc, 2)
            self.assertEqual(out["errors"][0]["code"], "NO_ESCALATION_MODEL")
        finally:
            os.remove(cfg)

    def test_escalation_rerun_mints_new_preview_id(self):
        # preview_id is content-derived, so re-analysing a day with the escalation
        # model (different worklog content) yields a different preview id — the
        # original preview is never overwritten in place.
        home = tempfile.mkdtemp(prefix="rw_pmhome_")
        try:
            def create(preview_sha):
                payload = json.dumps({
                    "repository": {"root": "/x", "branch": "main", "head": "abc",
                                   "worktree_fingerprint": None},
                    "worklog": {"index_sha256": "missing", "day_files": {},
                                "dir_fingerprint": "d", "preview_sha256": preview_sha},
                    "params": {"timezone": "Asia/Taipei", "include_uncommitted": False}})
                out, _, _ = run_script("preview_state.py",
                                       ["create", "--now", "2026-07-15T12:00:00+08:00"],
                                       stdin=payload, env={"HOME": home})
                return out["preview_id"]

            base_id = create("a" * 64)          # base-model dry-run
            escalated_id = create("b" * 64)     # escalation re-run, new content
            self.assertNotEqual(base_id, escalated_id)
        finally:
            import shutil
            shutil.rmtree(home, ignore_errors=True)


class TestConfigIsSingleSource(unittest.TestCase):
    """The JSON config is the one source; its defaults meet the acceptance bar."""

    def setUp(self):
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            self.config = json.load(fh)

    def test_defaults_are_cost_first(self):
        providers = self.config["providers"]
        for host, model_id in NEW_DEFAULTS.items():
            self.assertEqual(providers[host]["model_id"], model_id)
        self.assertEqual(providers["openai"]["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", providers["anthropic"])
        self.assertNotIn("reasoning_effort", providers["google"])

    def test_escalation_models_configured(self):
        providers = self.config["providers"]
        for host, esc in EXPECTED_ESCALATION.items():
            self.assertEqual(providers[host]["escalation_model_id"], esc)
        self.assertFalse(self.config["escalation_policy"]["automatic"])


class TestNoRetiredDefaults(unittest.TestCase):
    """Old default model names must not reappear as active defaults.

    Scoped to the shipped skill (git-worklog/). The historical design docs under
    docs/plans/ are intentionally excluded — they record pre-change plans.
    The old names claude-sonnet-5 / gpt-5.6-terra are permitted ONLY as escalation
    config; gemini-3-flash-preview (the retired google default) must be gone.
    """

    RETIRED_EVERYWHERE = ["gemini-3-flash-preview"]
    ESCALATION_ONLY = ["claude-sonnet-5", "gpt-5.6-terra", "gemini-3.1-pro-preview"]

    def _shipped_files(self):
        for dirpath, _dirs, names in os.walk(SKILL_DIR):
            if "__pycache__" in dirpath:
                continue
            for n in names:
                if n.endswith((".md", ".yaml", ".yml", ".json", ".py")):
                    yield os.path.join(dirpath, n)

    def test_retired_default_fully_absent(self):
        for path in self._shipped_files():
            text = _read(path)
            for token in self.RETIRED_EVERYWHERE:
                self.assertNotIn(
                    token, text,
                    f"retired model {token!r} still present in {path}")

    def test_old_names_only_in_escalation_context(self):
        heading = re.compile(r"^#{1,6}\s+(.*)$")
        for path in self._shipped_files():
            lines = _read(path).splitlines()
            current_heading = ""
            for line in lines:
                m = heading.match(line)
                if m:
                    current_heading = m.group(1).lower()
                for token in self.ESCALATION_ONLY:
                    if token in line:
                        ok = ("escalation" in line.lower()
                              or "escalation" in current_heading)
                        self.assertTrue(
                            ok,
                            f"{token!r} in {path} is not in an escalation context: {line!r}")


class TestHaltTemplateNamesRequestedModel(unittest.TestCase):
    """The user-facing halt-and-ask template names the requested model id."""

    def test_provider_models_doc_has_requested_model_line(self):
        doc = os.path.join(SKILL_DIR, "references", "provider-models.md")
        text = _read(doc)
        self.assertIn("Requested model:", text)
        self.assertIn("No fallback model was selected automatically", text)


if __name__ == "__main__":
    unittest.main()
