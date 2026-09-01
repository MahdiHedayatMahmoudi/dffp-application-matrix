from scientific_pdf_ingestion import (
    FIGURE_CAPTION_RE,
    TABLE_CAPTION_RE,
    extract_caption_candidates_from_lines,
    parse_number_from_caption,
)


def test_real_numbered_captions_are_detected():
    figure_lines = [
        "Figure 5. Interpolated entry day-of-year values for winter wheat.",
        "Figure 9: Weather indicator and approximate uncertainty.",
        "Figure B1 – Out-of-sample interpolation accuracy by phase.",
    ]
    table_lines = [
        "Table 4. SD-threshold selection for winter wheat.",
        "Table A1: Names and identifiers of DWD stages.",
        "Table C1 – SD-threshold selection across the crop family.",
    ]

    assert [x["number"] for x in extract_caption_candidates_from_lines(figure_lines, "figure")] == ["5", "9", "B1"]
    assert [x["number"] for x in extract_caption_candidates_from_lines(table_lines, "table")] == ["4", "A1", "C1"]


def test_body_references_are_not_detected_as_captions():
    figure_references = [
        "Figure 5 shows the interpolated DOY values for winter wheat.",
        "Figure 7 illustrates the derivation for the Uckermark district.",
        "Figure 8 shows the Germany-wide decomposition for 2018.",
        "Figure 9 extends the decomposition to the full record.",
        "Fig. 5), 1993–2025, following the window-extraction approach.",
    ]
    table_references = [
        "Table 4 illustrates this flexibility.",
        "Table A1), with the respective median MAE in parentheses.",
    ]

    assert extract_caption_candidates_from_lines(figure_references, "figure") == []
    assert extract_caption_candidates_from_lines(table_references, "table") == []


def test_docling_caption_number_parsing_remains_permissive():
    # Item-number parsing should not depend on the stricter native-caption rule.
    assert parse_number_from_caption("Figure 7 Derivation of the indicator", "figure") == "7"
    assert parse_number_from_caption("Table A1 Names and identifiers", "table") == "A1"


def test_strict_regex_does_not_accept_reference_parentheses():
    assert FIGURE_CAPTION_RE.match("Fig. 5), 1993–2025") is None
    assert TABLE_CAPTION_RE.match("Table A1), with the respective median MAE") is None
