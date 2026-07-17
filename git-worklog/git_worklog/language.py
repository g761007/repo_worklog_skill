"""BCP 47 language tags, and how a run decides which language to write in.

Roadmap §6.2. The rule this module exists to enforce: *source data never
chooses the output language*. A repository full of English commit messages,
English identifiers, English comments and an English README still produces a
``zh-TW`` worklog when the person asking wants ``zh-TW``. Nothing here reads
repository content, and nothing downstream may infer a language from it.

Resolution order (§6.2.1), highest priority first::

    user request → CLI argument → project config → agent host
        → conversation → environment → system locale → en

The user-request, agent-host and conversation tiers are not observable from a
process: only the agent hosting the run knows what was asked for, or what
language the conversation is in. So the agent resolves those tiers itself and
passes the answer down with ``--language``, declaring where it came from with
``--language-source`` (§6.2.5). This module resolves only the tiers a process
can actually see — config, environment, locale — and records the source
faithfully either way, so the manifest says *why* a language was chosen and not
merely which one.

Deliberately not implemented: RFC 5646 extensions (``-u-``/``-t-``) and
private-use subtags. The accepted grammar is language[-script][-region][-variant],
which covers every tag §6.2.4 contemplates. An extension-bearing tag is
rejected rather than silently truncated.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# The language of last resort (§6.2.1, §6.2.14). Reaching it is a warning, not
# a silent default: an agent that lands here is expected to re-run with an
# explicit --language rather than accept English.
FALLBACK = "en"

# Where a resolved language came from (§6.2.8). The first three are only ever
# reported by an agent passing --language-source; the rest this module sets.
SOURCES = (
    "user-request",
    "cli-argument",
    "project-config",
    "environment",
    "agent-host",
    "conversation",
    "system-locale",
    "fallback",
)

# The environment variable for standalone CLI use (§6.2.6). New in the language
# contract, so unlike GIT_WORKLOG_<HOST>_MODEL there is no legacy name to fall
# back to.
LANGUAGE_ENV = "GIT_WORKLOG_LANGUAGE"

# Consulted in order for the system-locale tier. Read directly rather than via
# locale.getdefaultlocale(), which is deprecated from Python 3.11 and reads
# these same variables anyway.
_LOCALE_ENV = ("LC_ALL", "LC_MESSAGES", "LANG")

# Locale values that carry no language intent — the default in containers and
# CI (§6.2.5). Treated as absent, not as English.
_EMPTY_LOCALES = {"C", "POSIX", ""}

# "auto" means "nobody has decided yet", in both config and --language. It is
# the shipped config default (markers.render_config), so on disk it is
# indistinguishable from a config the user never touched — which is exactly how
# it must be read.
AUTO = "auto"

# Syntactically valid BCP 47, but too coarse to write in: zh alone does not say
# Hant or Hans (§6.2.4). Rejected with a message naming the alternatives rather
# than guessed at.
_AMBIGUOUS = {
    "zh": ("zh-TW", "zh-CN"),
}

# The primary subtag is capped at 3 letters: every ISO 639 code is 2 (639-1) or
# 3 (639-2/3) letters. RFC 5646 also reserves 4-letter and registers 5-8-letter
# primary subtags, but none are in use, and allowing them would make "chinese"
# and "garbage" parse as languages — turning a typo or a junk locale into a
# confidently wrong output language instead of an error.
_TAG_RE = re.compile(
    r"^([A-Za-z]{2,3})"                                   # language
    r"(?:-([A-Za-z]{4}))?"                                # script
    r"(?:-([A-Za-z]{2}|[0-9]{3}))?"                       # region
    r"((?:-(?:[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*)$"  # variants
)


class LanguageError(ValueError):
    """A language value that cannot be honoured, carrying the wire code."""

    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


def normalize(tag: str) -> str:
    """Return ``tag`` in canonical BCP 47 casing, or raise LanguageError.

    Casing is normalised (``ZH-tw`` → ``zh-TW``) because tags are compared for
    equality across a run — manifest against result, preview against apply —
    and a case difference is not a language difference. What is *not* normalised
    away is the distinction between ``zh-TW`` and ``zh-CN``: they are separate
    languages here and comparing equal would be a bug (§21.4).
    """
    if not isinstance(tag, str):
        raise LanguageError("LANGUAGE_INVALID",
                            f"Language must be a string, got {type(tag).__name__}.")
    raw = tag.strip()
    if not raw:
        raise LanguageError("LANGUAGE_INVALID", "Language must not be empty.")

    m = _TAG_RE.match(raw)
    if not m:
        raise LanguageError(
            "LANGUAGE_INVALID",
            f"{raw!r} is not a BCP 47 language tag. Use a tag like 'zh-TW', "
            f"'en' or 'ja' — not a language name.",
            language=raw,
        )

    lang, script, region, variants = m.groups()
    out = lang.lower()
    if script:
        out += "-" + script.title()
    if region:
        out += "-" + region.upper()
    if variants:
        out += "".join("-" + v.lower() for v in variants.split("-") if v)

    if out in _AMBIGUOUS:
        options = " or ".join(_AMBIGUOUS[out])
        raise LanguageError(
            "LANGUAGE_AMBIGUOUS",
            f"{out!r} does not identify a written language; use {options}.",
            language=out,
        )
    return out


def is_valid(tag: str) -> bool:
    """True if ``tag`` is a language this tool will write in."""
    try:
        normalize(tag)
    except LanguageError:
        return False
    return True


def _from_locale() -> "str | None":
    """The system locale as a BCP 47 tag, or None if it carries no intent."""
    for var in _LOCALE_ENV:
        raw = os.environ.get(var)
        if not raw:
            continue
        value = raw.split(".")[0].split("@")[0].strip()
        if value in _EMPTY_LOCALES:
            continue
        try:
            return normalize(value.replace("_", "-"))
        except LanguageError:
            # A locale we cannot read is not an error the user caused; fall
            # through to the next variable, and ultimately to English.
            continue
    return None


@dataclass
class Resolution:
    """The language decision for one run, and the story of how it was made."""

    requested: "str | None"
    resolved: str
    source: str
    fallback: str = FALLBACK
    warnings: list = field(default_factory=list)

    def as_manifest(self) -> dict:
        """The ``language`` block for a manifest or a payload (§6.2.8)."""
        return {
            "requested": self.requested,
            "resolved": self.resolved,
            "source": self.source,
            "fallback": self.fallback,
        }


def resolve(
    explicit: "str | None" = None,
    source: "str | None" = None,
    config_value: "str | None" = None,
    allow_locale: bool = True,
) -> Resolution:
    """Decide the output language for a run (§6.2.1).

    ``explicit`` is ``--language``: either a tag, or ``auto`` / None meaning the
    caller has nothing to say. ``source`` is ``--language-source``, letting an
    agent declare that its tag came from the user rather than from a flag — the
    tiers above ``cli-argument`` exist only in the agent, so without this the
    manifest could not tell a user's explicit request apart from a default.

    ``allow_locale`` is False for agent-hosted runs, where guessing from the
    host OS is wrong (§6.2.5): a remote dev container is fixed to en_US and says
    nothing about what the user wants.
    """
    if source is not None and source not in SOURCES:
        raise LanguageError(
            "LANGUAGE_SOURCE_INVALID",
            f"{source!r} is not a language source. Expected one of: "
            f"{', '.join(SOURCES)}.",
            source=source,
        )

    requested = explicit if explicit else None

    if explicit and explicit.strip().lower() != AUTO:
        return Resolution(requested=requested,
                          resolved=normalize(explicit),
                          source=source or "cli-argument")

    # An explicit "auto" and an omitted flag mean the same thing — keep looking
    # — but "auto" is worth echoing back as what was requested.
    if config_value and config_value.strip().lower() != AUTO:
        return Resolution(requested=requested or config_value,
                          resolved=normalize(config_value),
                          source=source or "project-config")

    env_value = os.environ.get(LANGUAGE_ENV)
    if env_value and env_value.strip():
        return Resolution(requested=requested or env_value,
                          resolved=normalize(env_value),
                          source=source or "environment")

    if allow_locale:
        from_locale = _from_locale()
        if from_locale:
            return Resolution(requested=requested,
                              resolved=from_locale,
                              source=source or "system-locale")

    return Resolution(
        requested=requested,
        resolved=FALLBACK,
        source="fallback",
        warnings=[{
            "code": "LANGUAGE_NOT_RESOLVED",
            "message": "No output language was provided; falling back to English.",
            "fallback_language": FALLBACK,
        }],
    )


# --- interface language ------------------------------------------------------

# CLI status, error and prompt text (§6.2.13). Phase one ships English only —
# and that is explicitly allowed — but the flag is honoured rather than ignored:
# asking for an interface language we do not have gets a warning saying so, not
# silence that looks like success.
INTERFACE_SUPPORTED = ("en",)


def resolve_interface(explicit: "str | None" = None) -> Resolution:
    """Decide the language for the CLI's own messages (§6.2.13).

    Independent of the content language on purpose: ``--language zh-TW
    --interface-language en`` is a supported combination, and the worklog being
    zh-TW must never drag the CLI's diagnostics along with it, nor the reverse.
    """
    if not explicit or explicit.strip().lower() == AUTO:
        return Resolution(requested=explicit or None, resolved=FALLBACK,
                          source="fallback")

    tag = normalize(explicit)
    if tag in INTERFACE_SUPPORTED:
        return Resolution(requested=explicit, resolved=tag, source="cli-argument")

    return Resolution(
        requested=explicit,
        resolved=FALLBACK,
        source="fallback",
        warnings=[{
            "code": "INTERFACE_LANGUAGE_NOT_SUPPORTED",
            "message": (f"Interface messages are not available in {tag!r} yet; "
                        f"using {FALLBACK}. This does not affect worklog "
                        f"content language."),
            "requested_language": tag,
            "fallback_language": FALLBACK,
        }],
    )
