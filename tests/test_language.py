"""The BCP 47 language contract: tag handling and resolution order.

Covers the resolver half of roadmap §21.4. The scenarios that need a real
worklog on disk — MANUAL preservation, index stability, preview/apply
consistency, collector agreement — live with the engine and script tests that
own those surfaces.

Every test that touches resolution clears GIT_WORKLOG_LANGUAGE and the locale
variables first. Without that these pass or fail depending on the developer's
own shell, which is the exact class of bug the contract exists to prevent.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (bootstraps sys.path for the git_worklog package)

from git_worklog import language as lang

_VOLATILE = ("GIT_WORKLOG_LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")


def env(**overrides):
    """Patch the environment with every language-bearing variable cleared."""
    clean = {k: v for k, v in os.environ.items() if k not in _VOLATILE}
    clean.update({k: v for k, v in overrides.items() if v is not None})
    return mock.patch.dict(os.environ, clean, clear=True)


class TestNormalize(unittest.TestCase):
    def test_canonical_tags_survive_unchanged(self):
        for tag in ("en", "en-US", "zh-TW", "zh-CN", "ja", "ko", "de", "fr"):
            self.assertEqual(lang.normalize(tag), tag)

    def test_casing_is_normalised(self):
        # Tags get compared for equality across a run; case is not a language
        # difference and must not read as one.
        self.assertEqual(lang.normalize("ZH-tw"), "zh-TW")
        self.assertEqual(lang.normalize("EN-us"), "en-US")
        self.assertEqual(lang.normalize("  ja  "), "ja")

    def test_script_subtag_is_title_cased(self):
        self.assertEqual(lang.normalize("zh-hant-tw"), "zh-Hant-TW")

    def test_zh_tw_and_zh_cn_are_not_the_same_setting(self):
        # §21.4: collapsing these would silently write Simplified for a
        # Traditional reader.
        self.assertNotEqual(lang.normalize("zh-TW"), lang.normalize("zh-CN"))

    def test_bare_zh_is_rejected_as_ambiguous(self):
        with self.assertRaises(lang.LanguageError) as ctx:
            lang.normalize("zh")
        self.assertEqual(ctx.exception.code, "LANGUAGE_AMBIGUOUS")
        # The message must name the way out, not just refuse.
        self.assertIn("zh-TW", str(ctx.exception))
        self.assertIn("zh-CN", str(ctx.exception))

    def test_language_names_are_not_tags(self):
        for bad in ("chinese", "traditional", "Traditional Chinese", "中文"):
            with self.assertRaises(lang.LanguageError):
                lang.normalize(bad)

    def test_empty_and_non_string_rejected(self):
        for bad in ("", "   ", None, 42, ["zh-TW"]):
            with self.assertRaises(lang.LanguageError):
                lang.normalize(bad)

    def test_extension_subtags_are_rejected_not_truncated(self):
        # Silently dropping -u-co-pinyin would mean honouring a request we did
        # not actually understand.
        with self.assertRaises(lang.LanguageError):
            lang.normalize("zh-TW-u-co-pinyin")

    def test_is_valid_mirrors_normalize(self):
        self.assertTrue(lang.is_valid("zh-TW"))
        self.assertFalse(lang.is_valid("zh"))
        self.assertFalse(lang.is_valid("nonsense-tag-here"))


class TestResolutionOrder(unittest.TestCase):
    def test_explicit_request_wins(self):
        with env():
            r = lang.resolve(explicit="zh-TW")
        self.assertEqual(r.resolved, "zh-TW")
        self.assertEqual(r.source, "cli-argument")
        self.assertEqual(r.fallback, "en")

    def test_explicit_english_is_honoured_not_treated_as_default(self):
        # en must arrive with source=cli-argument, not source=fallback: the
        # difference is "the user asked for English" vs "we gave up".
        with env():
            r = lang.resolve(explicit="en")
        self.assertEqual(r.resolved, "en")
        self.assertEqual(r.source, "cli-argument")
        self.assertEqual(r.warnings, [])

    def test_agent_host_source_is_recorded(self):
        with env():
            r = lang.resolve(explicit="zh-TW", source="agent-host")
        self.assertEqual(r.resolved, "zh-TW")
        self.assertEqual(r.source, "agent-host")

    def test_user_request_beats_project_config(self):
        with env():
            r = lang.resolve(explicit="zh-TW", source="user-request",
                             config_value="ja")
        self.assertEqual(r.resolved, "zh-TW")
        self.assertEqual(r.source, "user-request")

    def test_cli_argument_beats_config(self):
        with env():
            r = lang.resolve(explicit="en", config_value="ja")
        self.assertEqual(r.resolved, "en")

    def test_config_used_when_no_explicit_request(self):
        with env():
            r = lang.resolve(config_value="ja")
        self.assertEqual(r.resolved, "ja")
        self.assertEqual(r.source, "project-config")

    def test_config_auto_means_undecided(self):
        # "auto" is the shipped default written to every config.json, so it can
        # never read as a choice.
        with env(GIT_WORKLOG_LANGUAGE="de"):
            r = lang.resolve(config_value="auto")
        self.assertEqual(r.resolved, "de")
        self.assertEqual(r.source, "environment")

    def test_explicit_auto_falls_through_to_config(self):
        with env():
            r = lang.resolve(explicit="auto", config_value="ja")
        self.assertEqual(r.resolved, "ja")
        self.assertEqual(r.source, "project-config")
        self.assertEqual(r.requested, "auto")

    def test_environment_beats_locale(self):
        with env(GIT_WORKLOG_LANGUAGE="ko", LANG="ja_JP.UTF-8"):
            r = lang.resolve()
        self.assertEqual(r.resolved, "ko")
        self.assertEqual(r.source, "environment")

    def test_config_beats_environment(self):
        with env(GIT_WORKLOG_LANGUAGE="ko"):
            r = lang.resolve(config_value="ja")
        self.assertEqual(r.resolved, "ja")
        self.assertEqual(r.source, "project-config")

    def test_locale_is_last_before_english(self):
        with env(LANG="zh_TW.UTF-8"):
            r = lang.resolve()
        self.assertEqual(r.resolved, "zh-TW")
        self.assertEqual(r.source, "system-locale")

    def test_lc_all_beats_lang(self):
        with env(LC_ALL="ja_JP.UTF-8", LANG="ko_KR.UTF-8"):
            r = lang.resolve()
        self.assertEqual(r.resolved, "ja-JP")

    def test_c_and_posix_locales_carry_no_language(self):
        # A container defaulting to C is not a user asking for English; it is a
        # user who has not been asked. The distinction shows up in `source`.
        for value in ("C", "POSIX", "C.UTF-8"):
            with env(LANG=value):
                r = lang.resolve()
            self.assertEqual(r.resolved, "en")
            self.assertEqual(r.source, "fallback", f"LANG={value}")

    def test_unreadable_locale_falls_through_rather_than_failing(self):
        with env(LANG="not_a_locale_at_all.UTF-8", LC_ALL="garbage@@@"):
            r = lang.resolve()
        self.assertEqual(r.resolved, "en")
        self.assertEqual(r.source, "fallback")

    def test_agent_hosted_runs_never_consult_the_host_locale(self):
        # §6.2.5: a dev container pinned to en_US says nothing about what the
        # user wants, so an agent-hosted run must fall back rather than guess.
        with env(LANG="en_US.UTF-8"):
            r = lang.resolve(allow_locale=False)
        self.assertEqual(r.resolved, "en")
        self.assertEqual(r.source, "fallback")
        self.assertEqual(r.warnings[0]["code"], "LANGUAGE_NOT_RESOLVED")

    def test_unresolvable_falls_back_to_english_with_a_warning(self):
        with env():
            r = lang.resolve()
        self.assertEqual(r.resolved, "en")
        self.assertEqual(r.source, "fallback")
        self.assertEqual(len(r.warnings), 1)
        self.assertEqual(r.warnings[0]["code"], "LANGUAGE_NOT_RESOLVED")
        self.assertEqual(r.warnings[0]["fallback_language"], "en")

    def test_invalid_explicit_language_raises_rather_than_falling_back(self):
        # Falling back here would quietly write English for someone who asked
        # for something specific and mistyped it.
        with env():
            with self.assertRaises(lang.LanguageError):
                lang.resolve(explicit="chinese")

    def test_invalid_source_is_rejected(self):
        with env():
            with self.assertRaises(lang.LanguageError) as ctx:
                lang.resolve(explicit="zh-TW", source="vibes")
        self.assertEqual(ctx.exception.code, "LANGUAGE_SOURCE_INVALID")

    def test_every_declared_source_is_accepted(self):
        for source in lang.SOURCES:
            with env():
                r = lang.resolve(explicit="zh-TW", source=source)
            self.assertEqual(r.source, source)


class TestManifestBlock(unittest.TestCase):
    def test_manifest_shape_matches_the_spec(self):
        with env():
            block = lang.resolve(explicit="auto", config_value="zh-TW",
                                 source="agent-host").as_manifest()
        self.assertEqual(block, {
            "requested": "auto",
            "resolved": "zh-TW",
            "source": "agent-host",
            "fallback": "en",
        })

    def test_keys_are_stable_english_regardless_of_language(self):
        # §6.2.13: JSON keys are API, not prose. They do not translate.
        with env():
            block = lang.resolve(explicit="ja").as_manifest()
        self.assertEqual(sorted(block), ["fallback", "requested", "resolved", "source"])


class TestInterfaceLanguage(unittest.TestCase):
    def test_english_is_supported(self):
        r = lang.resolve_interface("en")
        self.assertEqual(r.resolved, "en")
        self.assertEqual(r.warnings, [])

    def test_unsupported_interface_language_warns_rather_than_silently_ignoring(self):
        r = lang.resolve_interface("zh-TW")
        self.assertEqual(r.resolved, "en")
        self.assertEqual(len(r.warnings), 1)
        self.assertEqual(r.warnings[0]["code"], "INTERFACE_LANGUAGE_NOT_SUPPORTED")
        self.assertEqual(r.warnings[0]["requested_language"], "zh-TW")

    def test_omitted_interface_language_defaults_to_english_quietly(self):
        r = lang.resolve_interface(None)
        self.assertEqual(r.resolved, "en")
        self.assertEqual(r.warnings, [])

    def test_invalid_interface_language_still_raises(self):
        with self.assertRaises(lang.LanguageError):
            lang.resolve_interface("nonsense")

    def test_interface_language_is_independent_of_content_language(self):
        # §6.2.13's whole point: --language zh-TW --interface-language en is a
        # supported combination, and neither drags the other along.
        with env():
            content = lang.resolve(explicit="zh-TW")
        interface = lang.resolve_interface("en")
        self.assertEqual(content.resolved, "zh-TW")
        self.assertEqual(interface.resolved, "en")


if __name__ == "__main__":
    unittest.main()
