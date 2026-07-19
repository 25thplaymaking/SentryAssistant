"""The daily brief.

Assembles one short spoken-length sentence plus expandable, source-attributed
detail. Two properties matter more than the prose:

- **Only enabled sources contribute.** A disabled connector does not appear, does
  not get queried, and cannot leak into the summary by having been enabled
  yesterday.
- **Every claim keeps its source.** The one-sentence headline is a count, not an
  assertion about content, so it cannot outrun what the detail can support.

The brief is composed here from already-fetched items. Fetching lives in the
connectors; keeping the two apart means a source that is off can be proven not
to have been read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class BriefSource(StrEnum):
    WORK_ORDERS = "work_orders"
    REMINDERS = "reminders"
    GMAIL = "gmail"
    DISCORD = "discord"
    STEAM = "steam"
    APPROVALS = "approvals"


#: Sources that are part of Sentry itself and need no connector grant.
INTERNAL_SOURCES: frozenset[BriefSource] = frozenset(
    {BriefSource.WORK_ORDERS, BriefSource.REMINDERS, BriefSource.APPROVALS}
)


class BriefError(Exception):
    """Raised when a brief would include something it must not."""


@dataclass(frozen=True, slots=True)
class BriefItem:
    source: BriefSource
    headline: str
    #: Where this came from, shown beside the item so nothing is unattributed.
    attribution: str
    needs_attention: bool = False


@dataclass(frozen=True, slots=True)
class BriefPreferences:
    """Which sources this profile has switched on."""

    enabled: frozenset[BriefSource] = field(
        default_factory=lambda: frozenset(INTERNAL_SOURCES)
    )

    def allows(self, source: BriefSource) -> bool:
        return source in self.enabled


@dataclass(frozen=True, slots=True)
class DailyBrief:
    profile_id: str
    generated_at: datetime
    sentence: str
    items: tuple[BriefItem, ...]
    sources_used: tuple[BriefSource, ...]
    sources_skipped: tuple[BriefSource, ...]

    @property
    def attention_count(self) -> int:
        return sum(1 for item in self.items if item.needs_attention)

    def detail_lines(self) -> tuple[str, ...]:
        """Expandable detail. Every line carries its attribution."""
        return tuple(
            f"[{item.source.value}] {item.headline} — {item.attribution}"
            for item in self.items
        )


def _sentence(items: tuple[BriefItem, ...]) -> str:
    """One sentence, short enough to speak, and only ever a count.

    Summarising content here would let the headline assert more than the detail
    can support, so it deliberately does not try.
    """
    if not items:
        return "Nothing needs your attention."

    attention = sum(1 for item in items if item.needs_attention)
    if attention == 0:
        noun = "update" if len(items) == 1 else "updates"
        return f"{len(items)} {noun}, nothing needing your attention."

    # The verb agrees with the count; the noun is pluralised by whichever number
    # it actually follows. "1 of 2 items needs", not "1 of 2 item needs".
    verb = "needs" if attention == 1 else "need"

    if attention == len(items):
        noun = "item" if attention == 1 else "items"
        return f"{attention} {noun} {verb} your attention."

    return f"{attention} of {len(items)} items {verb} your attention."


def compose(
    *,
    profile_id: str,
    generated_at: datetime,
    candidates: list[BriefItem],
    preferences: BriefPreferences,
    granted_connectors: frozenset[BriefSource] | None = None,
) -> DailyBrief:
    """Build a brief from candidate items, dropping anything not enabled.

    `granted_connectors` is the set of external sources this profile actually
    holds a live grant for. A source that is switched on in preferences but has
    no grant is still excluded: preference is intent, a grant is capability, and
    the brief needs both.
    """
    grants = granted_connectors if granted_connectors is not None else frozenset()

    kept: list[BriefItem] = []
    used: set[BriefSource] = set()
    skipped: set[BriefSource] = set()

    for item in candidates:
        if not preferences.allows(item.source):
            skipped.add(item.source)
            continue

        # External sources additionally require a live connector grant.
        if item.source not in INTERNAL_SOURCES and item.source not in grants:
            skipped.add(item.source)
            continue

        if not item.attribution:
            # An unattributed item would appear in the brief with no way to
            # check it, which defeats the point of the detail section.
            raise BriefError(
                f"Brief item from {item.source.value} has no attribution."
            )

        kept.append(item)
        used.add(item.source)

    ordered = tuple(
        sorted(kept, key=lambda i: (not i.needs_attention, i.source.value))
    )

    return DailyBrief(
        profile_id=profile_id,
        generated_at=generated_at,
        sentence=_sentence(ordered),
        items=ordered,
        sources_used=tuple(sorted(used, key=lambda s: s.value)),
        sources_skipped=tuple(sorted(skipped, key=lambda s: s.value)),
    )
