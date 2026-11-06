// Date field with the shared calendar popover (Chinese holidays and make-up workdays marked). Used by the project
// drawer forms and the investment page's outcome form.
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { DayPicker } from "react-day-picker";
import { zhCN } from "date-fns/locale";
import { fetchChinaHolidayCalendar } from "../api";
import type { ChinaHolidayCalendarDay } from "../types";

export function parseDate(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

export function formatDate(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

type ChinaHolidayCalendarMarkers = Record<string, ChinaHolidayCalendarDay>;

let chinaHolidayCalendarCache: ChinaHolidayCalendarMarkers | null = null;
let chinaHolidayCalendarRequest: Promise<ChinaHolidayCalendarMarkers> | null = null;

function loadChinaHolidayCalendar(): Promise<ChinaHolidayCalendarMarkers> {
  if (chinaHolidayCalendarCache) return Promise.resolve(chinaHolidayCalendarCache);
  if (!chinaHolidayCalendarRequest) {
    chinaHolidayCalendarRequest = fetchChinaHolidayCalendar()
      .then(response => {
        const markers = Object.fromEntries(response.data.map(day => [day.date, day]));
        if (response.meta.available) chinaHolidayCalendarCache = markers;
        return markers;
      })
      .catch(() => ({}))
      .finally(() => { chinaHolidayCalendarRequest = null; });
  }
  return chinaHolidayCalendarRequest;
}

function useChinaHolidayCalendarMarkers(): ChinaHolidayCalendarMarkers {
  const [markers, setMarkers] = useState<ChinaHolidayCalendarMarkers>(chinaHolidayCalendarCache ?? {});
  useEffect(() => {
    let active = true;
    void loadChinaHolidayCalendar().then(days => { if (active) setMarkers(days); });
    return () => { active = false; };
  }, []);
  return markers;
}

export function CalendarDatePicker({ value, onChange, label, minDate, clearable = false }: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  minDate?: string;
  clearable?: boolean;
}) {
  const holidayMarkers = useChinaHolidayCalendarMarkers();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const selectedDate = parseDate(value) ?? undefined;
  const earliestDate = parseDate(minDate ?? "") ?? undefined;
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState<Date>(() => selectedDate ?? new Date());
  const [position, setPosition] = useState({ left: 12, top: 12, placement: "bottom" });
  const visibleYear = month.getFullYear();

  const positionPopover = useCallback(() => {
    const anchor = wrapperRef.current;
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const popoverRect = popoverRef.current?.getBoundingClientRect();
    const width = popoverRect?.width ?? 282;
    const height = popoverRect?.height ?? 310;
    const canOpenAbove = rect.top - height - 8 >= 12;
    const placement = rect.bottom + height + 8 > window.innerHeight - 12 && canOpenAbove ? "top" : "bottom";
    const top = placement === "top"
      ? Math.max(12, rect.top - height - 8)
      : Math.min(rect.bottom + 8, Math.max(12, window.innerHeight - height - 12));
    setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)), top, placement });
  }, []);

  useEffect(() => {
    if (selectedDate) setMonth(selectedDate);
  }, [value]);

  useEffect(() => {
    if (!open) return;
    const closeWhenOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!wrapperRef.current?.contains(target) && !popoverRef.current?.contains(target)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeWhenOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeWhenOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    positionPopover();
    const frame = window.requestAnimationFrame(positionPopover);
    window.addEventListener("resize", positionPopover);
    window.addEventListener("scroll", positionPopover, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", positionPopover);
      window.removeEventListener("scroll", positionPopover, true);
    };
  }, [open, positionPopover]);

  return <div className="date-picker" ref={wrapperRef}>
    <div className="date-picker-trigger">
      <input aria-label={label} value={selectedDate ? formatDate(selectedDate).replaceAll("-", "/") : ""} placeholder="请选择日期" readOnly onClick={() => setOpen(true)} />
      {clearable && selectedDate && <button type="button" className="date-picker-clear" aria-label="清除日期" onClick={() => onChange("")}>×</button>}
      <button type="button" className="date-picker-toggle" aria-label="打开日历" aria-expanded={open} onClick={() => setOpen(current => !current)}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3v3m10-3v3M4.5 9.5h15M6 5h12a1.5 1.5 0 0 1 1.5 1.5v12A1.5 1.5 0 0 1 18 20H6a1.5 1.5 0 0 1-1.5-1.5v-12A1.5 1.5 0 0 1 6 5Z" /><path d="M8 13h3m2 0h3m-8 3h3m2 0h3" /></svg>
      </button>
    </div>
    {open && createPortal(<div ref={popoverRef} className="day-picker-popover" data-placement={position.placement} role="dialog" aria-label={`${label}日历`} style={{ left: position.left, top: position.top }}>
      <DayPicker
        mode="single"
        selected={selectedDate}
        month={month}
        onMonthChange={setMonth}
        onSelect={date => { if (date) { onChange(formatDate(date)); setOpen(false); } }}
        disabled={earliestDate ? { before: earliestDate } : undefined}
        locale={zhCN}
        weekStartsOn={1}
        showOutsideDays
        fixedWeeks
        captionLayout="dropdown"
        navLayout="around"
        startMonth={new Date(visibleYear - 2, 0)}
        endMonth={new Date(visibleYear + 2, 11)}
        modifiers={{
          holiday: date => holidayMarkers[formatDate(date)]?.kind === "holiday",
          makeupWorkday: date => holidayMarkers[formatDate(date)]?.kind === "makeup_workday"
        }}
        modifiersClassNames={{ holiday: "rdp-day_holiday", makeupWorkday: "rdp-day_makeup-workday" }}
      />
      <div className="day-picker-legend" aria-label="日历标识说明"><span><i className="holiday" />法定节假日</span><span><i className="makeup" />调休补班</span></div>
    </div>, document.body)}
  </div>;
}
