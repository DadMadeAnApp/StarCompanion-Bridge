from ui.theme import qr_rows


def test_rows_are_square_and_scaled():
    rows = qr_rows("starcompanion://pair?v=2", scale=4)
    assert rows, "expected at least one row"
    width = len(rows[0].split())
    assert width == len(rows), "a QR code is square"
    assert width % 4 == 0, "every module is `scale` pixels wide"


def test_scale_multiplies_both_dimensions():
    small = qr_rows("hello", scale=1)
    big = qr_rows("hello", scale=3)
    assert len(big) == len(small) * 3
    assert len(big[0].split()) == len(small[0].split()) * 3


def test_only_black_and_white_pixels_are_emitted():
    colors = {c for row in qr_rows("hello", scale=2) for c in row.split()}
    assert colors <= {"#000000", "#ffffff"}


def test_a_quiet_border_surrounds_the_code():
    rows = qr_rows("hello", scale=1)
    assert set(rows[0].split()) == {"#ffffff"}
    assert set(rows[-1].split()) == {"#ffffff"}


def test_different_payloads_produce_different_bitmaps():
    assert qr_rows("one", scale=2) != qr_rows("two", scale=2)


def test_the_same_payload_is_stable():
    assert qr_rows("same", scale=2) == qr_rows("same", scale=2)
