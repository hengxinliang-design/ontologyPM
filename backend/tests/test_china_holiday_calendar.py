from app.china_holiday_calendar import ChinaHolidayCalendarService


REST_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:rest-one
DTSTART;VALUE=DATE:20261001
SUMMARY:国庆\\,中秋假期
END:VEVENT
BEGIN:VEVENT
UID:rest-two
DTSTART;TZID=Asia/Shanghai:20261002T000000
SUMMARY:国庆假期
END:VEVENT
END:VCALENDAR
"""

WORK_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:work-one
DTSTART;VALUE=DATE:20260927
SUMMARY:国庆补班
END:VEVENT
BEGIN:VEVENT
UID:overlap
DTSTART;VALUE=DATE:20261001
SUMMARY:错误补班
END:VEVENT
END:VCALENDAR
"""


def test_china_holiday_calendar_parses_dates_unescapes_names_and_caches():
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return REST_ICS if url == "rest" else WORK_ICS

    service = ChinaHolidayCalendarService("rest", "work", fetcher=fetch)
    first = service.list_days()
    second = service.list_days()

    assert first["meta"]["available"] is True
    assert first["meta"]["stale"] is False
    assert first["data"] == [
        {"date": "2026-09-27", "name": "国庆补班", "kind": "makeup_workday"},
        {"date": "2026-10-01", "name": "国庆,中秋假期", "kind": "holiday"},
        {"date": "2026-10-02", "name": "国庆假期", "kind": "holiday"},
    ]
    assert second["data"] == first["data"]
    assert calls == ["work", "rest"]


def test_china_holiday_calendar_gracefully_handles_unavailable_subscription():
    calls = 0

    def fetch(_: str) -> str:
        nonlocal calls
        calls += 1
        raise OSError("subscription unavailable")

    service = ChinaHolidayCalendarService("rest", "work", fetcher=fetch)
    result = service.list_days()
    repeated = service.list_days()

    assert result["data"] == []
    assert result["meta"]["available"] is False
    assert repeated["meta"]["available"] is False
    # A broken subscription must not be retried for every month in a resource
    # forecast; the failed empty calendar is cached for the configured TTL.
    assert calls == 1


def test_china_holiday_calendar_workday_summary_excludes_holidays_and_adds_makeup_days():
    def fetch(url: str) -> str:
        return REST_ICS if url == "rest" else WORK_ICS

    service = ChinaHolidayCalendarService("rest", "work", fetcher=fetch)

    summary = service.workday_summary("2026-09-26", "2026-10-02")

    # Sep 27 is an official Sunday makeup workday; Oct 1-2 are holidays.
    assert summary["working_days"] == 4
    assert summary["holiday_excluded_days"] == 2
    assert summary["makeup_workdays"] == 1
    assert summary["available"] is True
