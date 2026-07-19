from datetime import datetime, timezone

import pytest

from app.briefs.service import (
    INTERNAL_SOURCES,
    BriefError,
    BriefItem,
    BriefPreferences,
    BriefSource,
    compose,
)

NOW = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)


def item(source, headline="Something", attention=False, attribution="source"):
    return BriefItem(
        source=source,
        headline=headline,
        attribution=attribution,
        needs_attention=attention,
    )


def brief(candidates, preferences=None, grants=None):
    return compose(
        profile_id="p-1",
        generated_at=NOW,
        candidates=candidates,
        preferences=preferences or BriefPreferences(),
        granted_connectors=grants,
    )


class TestSourceGating:
    def test_disabled_sources_are_excluded(self):
        result = brief(
            [item(BriefSource.WORK_ORDERS), item(BriefSource.GMAIL)],
            preferences=BriefPreferences(enabled=frozenset({BriefSource.WORK_ORDERS})),
        )
        assert BriefSource.GMAIL not in result.sources_used
        assert BriefSource.GMAIL in result.sources_skipped
        assert all(i.source is BriefSource.WORK_ORDERS for i in result.items)

    # Preference is intent; a grant is capability. The brief needs both.
    def test_enabled_but_ungranted_connector_is_excluded(self):
        result = brief(
            [item(BriefSource.GMAIL)],
            preferences=BriefPreferences(
                enabled=frozenset(INTERNAL_SOURCES | {BriefSource.GMAIL})
            ),
            grants=frozenset(),
        )
        assert not result.items
        assert BriefSource.GMAIL in result.sources_skipped

    def test_enabled_and_granted_connector_is_included(self):
        result = brief(
            [item(BriefSource.GMAIL, attribution="3 unread from billing")],
            preferences=BriefPreferences(
                enabled=frozenset(INTERNAL_SOURCES | {BriefSource.GMAIL})
            ),
            grants=frozenset({BriefSource.GMAIL}),
        )
        assert BriefSource.GMAIL in result.sources_used
        assert len(result.items) == 1

    def test_internal_sources_need_no_grant(self):
        result = brief([item(BriefSource.WORK_ORDERS), item(BriefSource.REMINDERS)])
        assert set(result.sources_used) == {
            BriefSource.WORK_ORDERS,
            BriefSource.REMINDERS,
        }

    def test_defaults_exclude_every_external_connector(self):
        external = [
            item(BriefSource.GMAIL),
            item(BriefSource.DISCORD),
            item(BriefSource.STEAM),
        ]
        assert not brief(external).items


class TestAttribution:
    def test_every_detail_line_carries_its_source(self):
        result = brief(
            [
                item(BriefSource.WORK_ORDERS, "Two orders ready", attribution="gateway"),
                item(BriefSource.REMINDERS, "Stand-up at 09:00", attribution="reminder"),
            ]
        )
        lines = result.detail_lines()
        assert len(lines) == 2
        for line in lines:
            assert line.startswith("[")
            assert "—" in line

    def test_an_unattributed_item_is_refused(self):
        with pytest.raises(BriefError, match="no attribution"):
            brief([item(BriefSource.WORK_ORDERS, attribution="")])


class TestSentence:
    def test_empty_brief_says_so(self):
        assert brief([]).sentence == "Nothing needs your attention."

    def test_singular_and_plural_updates(self):
        assert "1 update," in brief([item(BriefSource.WORK_ORDERS)]).sentence
        assert "2 updates," in brief(
            [item(BriefSource.WORK_ORDERS), item(BriefSource.REMINDERS)]
        ).sentence

    def test_attention_is_counted(self):
        result = brief(
            [
                item(BriefSource.WORK_ORDERS, attention=True),
                item(BriefSource.REMINDERS, attention=False),
            ]
        )
        # Verb agrees with the count, noun with the total it follows.
        assert result.sentence == "1 of 2 items needs your attention."
        assert result.attention_count == 1

    def test_partial_attention_plural_verb(self):
        result = brief(
            [
                item(BriefSource.WORK_ORDERS, attention=True),
                item(BriefSource.APPROVALS, attention=True),
                item(BriefSource.REMINDERS, attention=False),
            ]
        )
        assert result.sentence == "2 of 3 items need your attention."

    def test_all_attention_reads_naturally(self):
        result = brief(
            [
                item(BriefSource.WORK_ORDERS, attention=True),
                item(BriefSource.APPROVALS, attention=True),
            ]
        )
        assert result.sentence == "2 items need your attention."

    def test_single_attention_is_singular(self):
        result = brief([item(BriefSource.APPROVALS, attention=True)])
        assert result.sentence == "1 item needs your attention."

    # The headline is a count, never a claim about content, so it cannot assert
    # more than the attributed detail supports.
    def test_the_sentence_never_quotes_item_content(self):
        secret_headline = "Password reset link for admin@example.com"
        result = brief([item(BriefSource.WORK_ORDERS, secret_headline)])
        assert "admin@example.com" not in result.sentence
        assert "Password" not in result.sentence

    def test_the_sentence_stays_short_enough_to_speak(self):
        many = [item(BriefSource.WORK_ORDERS, f"item {n}", attention=True) for n in range(50)]
        assert len(brief(many).sentence) < 60


class TestOrdering:
    def test_attention_items_come_first(self):
        result = brief(
            [
                item(BriefSource.STEAM if False else BriefSource.REMINDERS, attention=False),
                item(BriefSource.WORK_ORDERS, attention=True),
            ]
        )
        assert result.items[0].needs_attention
        assert not result.items[-1].needs_attention
