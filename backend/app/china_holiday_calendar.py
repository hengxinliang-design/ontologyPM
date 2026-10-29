from __future__ import annotations

import logging
import threading
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable


logger = logging.getLogger(__name__)


class ChinaHolidayCalendarService:
    """Read and cache China's public-holiday and make-up-workday iCalendars."""

    def __init__(
        self,
        rest_url: str,
        work_url: str,
        cache_hours: int = 12,
        fetcher: Callable[[str], str] | None = None,
    ) -> None:
        self.rest_url = rest_url
        self.work_url = work_url
        self.cache_ttl = timedelta(hours=cache_hours)
        self._fetcher = fetcher or self._download
        self._cache: list[dict[str, str]] | None = None
        self._cached_at: datetime | None = None
        self._available: bool | None = None
        self._stale = False
        self._lock = threading.Lock()

    def list_days(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._lock:
            if self._cache is not None and self._cached_at and now - self._cached_at < self.cache_ttl:
                return self._response(
                    self._cache,
                    available=bool(self._available),
                    stale=self._stale,
                )
            try:
                days = self._load_days()
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                logger.warning("unable to refresh Chinese holiday calendar: %s", exc)
                if self._cache is not None:
                    self._stale = True
                    return self._response(
                        self._cache,
                        available=bool(self._available),
                        stale=True,
                    )
                # Cache a failed first attempt too.  A dashboard request can
                # ask for several months; retrying a broken subscription for
                # every one would make the fallback slower and noisier.
                self._cache = []
                self._cached_at = now
                self._available = False
                self._stale = False
                return self._response(self._cache, available=False, stale=False)
            self._cache = days
            self._cached_at = now
            self._available = True
            self._stale = False
            return self._response(days, available=True, stale=False)

    def workday_summary(self, start: str | date, end: str | date) -> dict[str, Any]:
        """Count statutory working days for a closed date range.

        Weekdays are working days by default. Public holidays override weekdays
        as non-working days, while official make-up workdays override weekends.
        If the subscription is temporarily unavailable, the calculation stays
        usable with the weekday-only rule and identifies that fallback in meta.
        """
        start_date = _as_date(start)
        end_date = _as_date(end)
        if start_date > end_date:
            raise ValueError("start must not be after end")
        response = self.list_days()
        overrides = {item["date"]: item["kind"] for item in response["data"]}
        current = start_date
        working_days = 0
        holiday_excluded_days = 0
        makeup_workdays = 0
        while current <= end_date:
            default_workday = current.weekday() < 5
            override = overrides.get(current.isoformat())
            if override == "holiday":
                is_workday = False
                if default_workday:
                    holiday_excluded_days += 1
            elif override == "makeup_workday":
                is_workday = True
                if not default_workday:
                    makeup_workdays += 1
            else:
                is_workday = default_workday
            if is_workday:
                working_days += 1
            current += timedelta(days=1)
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "working_days": working_days,
            "holiday_excluded_days": holiday_excluded_days,
            "makeup_workdays": makeup_workdays,
            "available": response["meta"]["available"],
            "stale": response["meta"]["stale"],
            "source": response["meta"]["source"],
        }

    def _response(self, days: list[dict[str, str]], *, available: bool, stale: bool) -> dict[str, Any]:
        return {
            "data": days,
            "meta": {
                "available": available,
                "stale": stale,
                "cached_at": self._cached_at.isoformat(timespec="seconds") if self._cached_at else None,
                "source": "ical.muhan.org",
            },
        }

    def _load_days(self) -> list[dict[str, str]]:
        # A holiday wins in the unlikely event that upstream files contain the same date.
        by_date = {
            day["date"]: day
            for day in self._parse_ics(self._fetcher(self.work_url), "makeup_workday")
        }
        by_date.update({
            day["date"]: day
            for day in self._parse_ics(self._fetcher(self.rest_url), "holiday")
        })
        return [by_date[key] for key in sorted(by_date)]

    @staticmethod
    def _download(url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/calendar, text/plain;q=0.9",
                "User-Agent": "TSG-PM-Calendar/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            return response.read().decode("utf-8-sig")

    @staticmethod
    def _parse_ics(payload: str, kind: str) -> list[dict[str, str]]:
        unfolded: list[str] = []
        for raw_line in payload.replace("\r\n", "\n").split("\n"):
            if raw_line.startswith((" ", "\t")) and unfolded:
                unfolded[-1] += raw_line[1:]
            else:
                unfolded.append(raw_line)

        events: list[dict[str, str]] = []
        event: dict[str, str] | None = None
        for line in unfolded:
            if line == "BEGIN:VEVENT":
                event = {}
                continue
            if line == "END:VEVENT":
                if event and event.get("date"):
                    events.append({
                        "date": event["date"],
                        "name": event.get("name") or ("补班" if kind == "makeup_workday" else "节假日"),
                        "kind": kind,
                    })
                event = None
                continue
            if event is None or ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.startswith("DTSTART"):
                compact_date = value.strip()[:8]
                if len(compact_date) == 8 and compact_date.isdigit():
                    event["date"] = f"{compact_date[:4]}-{compact_date[4:6]}-{compact_date[6:]}"
            elif key == "SUMMARY":
                event["name"] = ChinaHolidayCalendarService._unescape_ics(value.strip())
        return events

    @staticmethod
    def _unescape_ics(value: str) -> str:
        return value.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _as_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)
