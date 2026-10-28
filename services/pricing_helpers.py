def to_arabic_indic(n: int) -> str:
    """Converts a non-negative integer to its Arabic-Indic numeral string."""
    digits = {
        "0": "٠",
        "1": "١",
        "2": "٢",
        "3": "٣",
        "4": "٤",
        "5": "٥",
        "6": "٦",
        "7": "٧",
        "8": "٨",
        "9": "٩",
    }
    return "".join(digits[d] for d in str(n))

def format_duration(minutes: int) -> str:
    """Converts integer minutes to a localized Arabic duration string."""
    if minutes < 60:
        if minutes == 0:
            return "٠ دقائق"
        elif minutes == 1:
            return "دقيقة واحدة"
        elif minutes == 2:
            return "دقيقتان"
        elif 3 <= minutes <= 10:
            return f"{to_arabic_indic(minutes)} دقائق"
        else:
            return f"{to_arabic_indic(minutes)} دقيقة"
    
    h = minutes // 60
    m = minutes % 60

    if h == 1:
        h_str = "ساعة واحدة" if m == 0 else "ساعة"
    elif h == 2:
        h_str = "ساعتان"
    elif 3 <= h <= 10:
        h_str = f"{to_arabic_indic(h)} ساعات"
    else:
        h_str = f"{to_arabic_indic(h)} ساعة"

    if m == 0:
        return h_str
    
    if m == 1:
        m_str = "دقيقة واحدة"
    elif m == 2:
        m_str = "دقيقتان"
    elif 3 <= m <= 10:
        m_str = f"{to_arabic_indic(m)} دقائق"
    else:
        m_str = f"{to_arabic_indic(m)} دقيقة"

    return f"{h_str} و{m_str}"

def format_egp(piastres: int) -> str:
    """Converts integer piastres to EGP display string with Arabic-Indic numerals."""
    egp = piastres // 100
    fils = piastres % 100
    val_str = f"{egp}.{fils:02d}"
    trans = str.maketrans("0123456789.", "٠١٢٣٤٥٦٧٨٩٫")
    return val_str.translate(trans) + " ج.م"

__all__ = ["format_duration", "format_egp", "to_arabic_indic"]
