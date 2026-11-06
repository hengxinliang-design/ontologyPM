// Month field with the app's month calendar, shared by every page that picks a month (项目管理, 资源运营监控,
// 员工工时数据, 投入成本分析, 部门经营) so they all look and behave the same.
import DatePicker, { registerLocale } from "react-datepicker";
import { zhCN } from "date-fns/locale";
import "react-datepicker/dist/react-datepicker.css";

registerLocale("zh-CN", zhCN);

export function parseMonth(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, 1);
}

export function formatMonth(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`;
}


export function MonthPicker({ value, onChange, label, min, max }: { value: string; onChange: (value: string) => void; label: string; min?: string; max?: string }) {
  return <div className="month-picker">
    <DatePicker
      aria-label={label}
      selected={parseMonth(value)}
      onChange={(date: Date | null) => onChange(date ? formatMonth(date) : "")}
      dateFormat="yyyy年MM月"
      locale="zh-CN"
      placeholderText="选择月份"
      showMonthYearPicker
      showFullMonthYearPicker
      showIcon
      toggleCalendarOnIconClick
      calendarStartDay={1}
      popperPlacement="bottom-start"
      minDate={min ? parseMonth(min) ?? undefined : undefined}
      maxDate={max ? parseMonth(max) ?? undefined : undefined}
    />
  </div>;
}
