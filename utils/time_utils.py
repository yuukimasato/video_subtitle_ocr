# utils/time_utils.py

def format_time(total_seconds: float) -> str:
    if total_seconds < 0:
        total_seconds = 0
    h = int(total_seconds / 3600)
    m = int((total_seconds % 3600) / 60)
    s = int(total_seconds % 60)
    ms = int((total_seconds - int(total_seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

def parse_time(time_str: str) -> float:

    parts = time_str.replace(',', '.').split(':')
    if len(parts) == 3:
        h, m, s_ms = parts
        s_parts = s_ms.split('.')
        s = int(s_parts[0])
        ms = int(s_parts[1]) if len(s_parts) > 1 else 0
        return float(h) * 3600 + float(m) * 60 + float(s) + ms / 1000.0
    elif len(parts) == 2:
        m, s_ms = parts
        s_parts = s_ms.split('.')
        s = int(s_parts[0])
        ms = int(s_parts[1]) if len(s_parts) > 1 else 0
        return float(m) * 60 + float(s) + ms / 1000.0
    else:
        return float(time_str)

