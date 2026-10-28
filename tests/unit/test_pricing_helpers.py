import inspect
from services.pricing_helpers import to_arabic_indic, format_egp, format_duration

def test_zero():
    assert to_arabic_indic(0) == "٠"

def test_single_digit():
    assert to_arabic_indic(5) == "٥"

def test_multi_digit():
    assert to_arabic_indic(2025) == "٢٠٢٥"

def test_zero_piastres():
    assert format_egp(0) == "٠٫٠٠ ج.م"

def test_round_egp():
    assert format_egp(100) == "١٫٠٠ ج.م"

def test_partial_egp():
    assert format_egp(2550) == "٢٥٫٥٠ ج.م"

def test_no_float_used():
    source = inspect.getsource(format_egp)
    assert "float" not in source

def test_arabic_decimal_separator():
    assert "٫" in format_egp(1234)

def test_zero_minutes():
    assert format_duration(0) == "٠ دقائق"

def test_one_minute():
    assert format_duration(1) == "دقيقة واحدة"

def test_two_minutes():
    assert format_duration(2) == "دقيقتان"

def test_five_minutes():
    assert format_duration(5) == "٥ دقائق"

def test_fifteen_minutes():
    assert format_duration(15) == "١٥ دقيقة"

def test_sixty_minutes():
    assert format_duration(60) == "ساعة واحدة"

def test_ninety_minutes():
    assert format_duration(90) == "ساعة و٣٠ دقيقة"

def test_one_twenty_minutes():
    assert format_duration(120) == "ساعتان"

def test_one_twenty_five_minutes():
    # standard rule for 2 hours and 5 minutes: "ساعتان و٥ دقائق"
    assert format_duration(125) == "ساعتان و٥ دقائق"

def test_two_hundred_minutes():
    # 200 minutes = 3 hours and 20 minutes: "٣ ساعات و٢٠ دقيقة"
    res = format_duration(200)
    assert "ساعات" in res
    assert "٢٠" in res

def test_exact_three_hours():
    # 180 minutes = 3 hours: "٣ ساعات"
    res = format_duration(180)
    assert "ساعات" in res
    assert "دقيقة" not in res
