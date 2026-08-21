"""The cron matcher decides when people's jobs fire; every clause gets pinned.

The subtle one is the day rule: standard cron gives dom and dow OR-semantics
when BOTH are restricted, and AND-semantics otherwise. Getting that wrong makes
"the 13th, and also every Friday" silently mean "only Friday the 13th".
"""

from datetime import datetime

import pytest

from app.cron.scheduler import CronSchedule


def at(month, day, hour, minute, year=2026):
    return datetime(year, month, day, hour, minute)


class TestBasicFields:
    def test_wildcards_match_any_minute(self):
        s = CronSchedule.parse("* * * * *")
        assert s.matches(at(1, 1, 0, 0))
        assert s.matches(at(12, 31, 23, 59))

    def test_exact_minute_and_hour(self):
        s = CronSchedule.parse("30 8 * * *")
        assert s.matches(at(6, 15, 8, 30))
        assert not s.matches(at(6, 15, 8, 31))
        assert not s.matches(at(6, 15, 9, 30))

    def test_exact_month_and_day(self):
        s = CronSchedule.parse("0 0 1 7 *")
        assert s.matches(at(7, 1, 0, 0))
        assert not s.matches(at(8, 1, 0, 0))
        assert not s.matches(at(7, 2, 0, 0))


class TestSteps:
    def test_star_step(self):
        s = CronSchedule.parse("*/15 * * * *")
        for minute in (0, 15, 30, 45):
            assert s.matches(at(1, 1, 0, minute))
        assert not s.matches(at(1, 1, 0, 20))

    def test_range_step(self):
        s = CronSchedule.parse("10-30/10 * * * *")
        assert s.matches(at(1, 1, 0, 10))
        assert s.matches(at(1, 1, 0, 30))
        assert not s.matches(at(1, 1, 0, 40))

    def test_number_step_runs_to_field_max(self):
        # Vixie shorthand: "20/15" == "20-59/15" in the minute field.
        s = CronSchedule.parse("20/15 * * * *")
        assert s.matches(at(1, 1, 0, 20))
        assert s.matches(at(1, 1, 0, 35))
        assert not s.matches(at(1, 1, 0, 15))


class TestListsAndRanges:
    def test_comma_list(self):
        s = CronSchedule.parse("0 6,18 * * *")
        assert s.matches(at(1, 1, 6, 0))
        assert s.matches(at(1, 1, 18, 0))
        assert not s.matches(at(1, 1, 12, 0))

    def test_range(self):
        s = CronSchedule.parse("0 9-17 * * *")
        assert s.matches(at(1, 1, 9, 0))
        assert s.matches(at(1, 1, 17, 0))
        assert not s.matches(at(1, 1, 18, 0))

    def test_list_mixing_numbers_ranges_and_steps(self):
        s = CronSchedule.parse("5,10-12,*/30 * * * *")
        for minute in (0, 5, 10, 11, 12, 30):
            assert s.matches(at(1, 1, 0, minute))
        assert not s.matches(at(1, 1, 0, 13))


class TestDayOfWeek:
    def test_both_0_and_7_are_sunday(self):
        sunday = at(8, 16, 0, 0)  # 2026-08-16 is a Sunday
        assert CronSchedule.parse("0 0 * * 0").matches(sunday)
        assert CronSchedule.parse("0 0 * * 7").matches(sunday)
        assert not CronSchedule.parse("0 0 * * 1").matches(sunday)

    def test_weekday_range(self):
        s = CronSchedule.parse("0 9 * * 1-5")
        assert s.matches(at(8, 17, 9, 0))       # Monday
        assert s.matches(at(8, 21, 9, 0))       # Friday
        assert not s.matches(at(8, 22, 9, 0))   # Saturday


class TestDomDowOrRule:
    def test_both_restricted_means_either_matches(self):
        # "the 13th, or any Friday" -- NOT "only Friday the 13th".
        s = CronSchedule.parse("0 0 13 * 5")
        assert s.matches(at(8, 13, 0, 0))   # 13th, a Thursday
        assert s.matches(at(8, 21, 0, 0))   # Friday, not the 13th
        assert not s.matches(at(8, 20, 0, 0))  # Thursday the 20th

    def test_only_dom_restricted_must_match_dom(self):
        s = CronSchedule.parse("0 0 13 * *")
        assert s.matches(at(8, 13, 0, 0))
        assert not s.matches(at(8, 21, 0, 0))  # a Friday, but dow is *

    def test_only_dow_restricted_must_match_dow(self):
        s = CronSchedule.parse("0 0 * * 5")
        assert s.matches(at(8, 21, 0, 0))      # Friday
        assert not s.matches(at(8, 13, 0, 0))  # the 13th, but dom is *


class TestRejection:
    @pytest.mark.parametrize("text", [
        "",                  # no fields
        "* * * *",           # four fields
        "* * * * * *",       # six fields
        "60 * * * *",        # minute out of range
        "* 24 * * *",        # hour out of range
        "* * 0 * *",         # dom below range
        "* * 32 * *",        # dom above range
        "* * * 13 *",        # month out of range
        "* * * * 8",         # dow above range
        "5-1 * * * *",       # reversed range
        "*/0 * * * *",       # zero step
        "a * * * *",         # not a number
        "1,,2 * * * *",      # empty list element
    ])
    def test_malformed_schedules_raise(self, text):
        with pytest.raises(ValueError):
            CronSchedule.parse(text)
