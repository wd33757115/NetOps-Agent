"""Validity windows (start_time/end_time) and persistent-connection hours of a request.

A window is two minutes of device wall-clock time, ``(start, end)`` as "YYYY-MM-DD HH:MM";
the end is when the policy stops, so "through 12/31" ends at 01/01 00:00. Devices name the
time range after the ticket, so a ticket carries at most one window.
"""

import re
from datetime import datetime, timedelta

FORMAT = "%Y-%m-%d %H:%M"
LONG_LINK_DEFAULT = 168
LONG_LINK_MAX = 24000
YES = {"是", "y", "yes", "true", "开启", "启用", "enable"}
NO = {"否", "n", "no", "false", "关闭", "不启用", "disable"}
# Both vendors take 1-32 characters; H3C reserves "all". A leading letter suits both.
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")
_MOMENT = re.compile(
    r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)


def moment(text, *, end):
    """A request cell as a minute. For end_time a date alone, or midnight, means that whole day."""
    match = _MOMENT.fullmatch(text.strip())
    if not match:
        raise ValueError(f"无法识别的时间“{text}”，请写成 2026-10-23 或 2026-10-23 08:00")
    year, month, day, hour, minute, second = match.groups()
    if second and int(second):
        raise ValueError("有效期精确到分钟，请去掉秒")
    try:
        value = datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0))
    except ValueError as exc:
        raise ValueError(f"无效的日期或时间“{text}”") from exc
    if end and (value.hour, value.minute) == (0, 0):
        value += timedelta(days=1)
    return value


def window(start_text, end_text, now):
    """The row's validity window, or None when both cells are empty."""
    if not start_text and not end_text:
        return None
    if not (start_text and end_text):
        raise ValueError("start_time 和 end_time 必须同时填写")
    start, end = moment(start_text, end=False), moment(end_text, end=True)
    if end <= start:
        raise ValueError("end_time 必须晚于 start_time")
    if end <= now:
        raise ValueError("有效期已经结束")
    return start.strftime(FORMAT), end.strftime(FORMAT)


def long_link(text):
    """Persistent-connection aging hours, or None when not requested. 0 never ages."""
    value = text.strip().lower()
    if not value or value in NO:
        return None
    if value in YES:
        return LONG_LINK_DEFAULT
    if not re.fullmatch(r"\d+(?:\.0+)?", value):
        raise ValueError("long_link 填写老化小时数（0–24000，0 表示永不老化）或“是”（168 小时）")
    hours = int(float(value))
    if hours > LONG_LINK_MAX:
        raise ValueError(f"long_link 最大 {LONG_LINK_MAX} 小时")
    return hours


def display(value):
    """A window for people: the end of a day reads 24:00 of that day."""
    if not value:
        return ""
    start, end = (datetime.strptime(v, FORMAT) for v in value)
    if (end.hour, end.minute) == (0, 0):
        end_text = f"{end - timedelta(days=1):%Y-%m-%d} 24:00"
    else:
        end_text = f"{end:%Y-%m-%d %H:%M}"
    return f"{start:%Y-%m-%d %H:%M} 至 {end_text}"


# Rules without a window or long link keep the five-part context they always had. Inside the
# context "none" is () and -1, so contexts stay sortable against each other.
def extend(context, value, hours):
    if not value and hours is None:
        return tuple(context)
    return (*context, (tuple(value or ()), -1 if hours is None else hours))


def settings(context):
    if len(context) <= 5:
        return None, None
    value, hours = context[5]
    return (value or None), (None if hours < 0 else hours)


def huawei_range(value):
    """USG6000 time-range view statement."""
    start, end = (datetime.strptime(v, FORMAT) for v in value)
    last = end - timedelta(seconds=1) if (end.hour, end.minute) == (0, 0) else end
    return f"absolute-range {start:%H:%M:%S %Y/%m/%d} to {last:%H:%M:%S %Y/%m/%d}"


def h3c_range(name, value):
    """Comware V7 system-view time-range command (YYYY/MM/DD dates, end may be 24:00)."""
    start, end = (datetime.strptime(v, FORMAT) for v in value)
    if (end.hour, end.minute) == (0, 0):
        end_text = f"24:00 {end - timedelta(days=1):%Y/%m/%d}"
    else:
        end_text = f"{end:%H:%M %Y/%m/%d}"
    return f"time-range {name} from {start:%H:%M %Y/%m/%d} to {end_text}"


def read_huawei(line):
    match = re.fullmatch(
        r"absolute-range (\d{2}:\d{2}:\d{2}) (\d{4}/\d{2}/\d{2}) to (\d{2}:\d{2}:\d{2}) (\d{4}/\d{2}/\d{2})",
        line,
    )
    if not match:
        raise ValueError("无法回读时间段命令")
    start = datetime.strptime(f"{match[2]} {match[1]}", "%Y/%m/%d %H:%M:%S")
    end = datetime.strptime(f"{match[4]} {match[3]}", "%Y/%m/%d %H:%M:%S")
    if start.second or end.second not in (0, 59):
        raise ValueError("无法回读时间段命令")
    if end.second == 59:
        end += timedelta(seconds=1)
    return start.strftime(FORMAT), end.strftime(FORMAT)


def read_h3c(line):
    """(name, window) of a Comware time-range command."""
    match = re.fullmatch(
        r"time-range (\S+) from (\d{1,2}:\d{2}) (\d{4}/\d{1,2}/\d{1,2}) to (\d{1,2}:\d{2}) (\d{4}/\d{1,2}/\d{1,2})",
        line,
    )
    if not match:
        raise ValueError("无法回读时间段命令")
    name, start_time, start_date, end_time, end_date = match.groups()
    start = datetime.strptime(f"{start_date} {start_time}", "%Y/%m/%d %H:%M")
    if end_time == "24:00":
        end = datetime.strptime(end_date, "%Y/%m/%d") + timedelta(days=1)
    else:
        end = datetime.strptime(f"{end_date} {end_time}", "%Y/%m/%d %H:%M")
    return name, (start.strftime(FORMAT), end.strftime(FORMAT))
