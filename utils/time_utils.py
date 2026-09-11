# utils/time_utils.py

def format_time(total_seconds: float) -> str:
    # 先换算到整毫秒再取位，保证进位正确（如 59.9999 -> 00:01:00.000）。
    if total_seconds < 0:
        total_seconds = 0.0
    ms_total = int(round(float(total_seconds) * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _parse_seconds_with_ms(s_ms: str) -> float:
    s_parts = s_ms.split(".")
    sec = float(s_parts[0])
    ms = 0.0
    if len(s_parts) > 1 and s_parts[1]:
        # ".5" 语义是 500ms：右补齐到 3 位按毫秒解释（"05" -> 50ms）。
        ms = float(s_parts[1].ljust(3, "0")[:3]) / 1000.0
    return sec + ms


def parse_time(time_str: str) -> float:

    s = str(time_str).strip().replace(",", ".")
    parts = s.split(":")
    try:
        if len(parts) == 3:
            h, m, s_ms = parts
            return float(h) * 3600 + float(m) * 60 + _parse_seconds_with_ms(s_ms)
        elif len(parts) == 2:
            m, s_ms = parts
            return float(m) * 60 + _parse_seconds_with_ms(s_ms)
        else:
            return float(s)
    except ValueError:
        raise ValueError(f"invalid time string: {time_str!r}")
