import pytest

from enron_pipeline.body import extract_headings, mentions_attachment, split_body


# --- split_body --------------------------------------------------------------

def test_plain_body_has_no_forwarded_or_quoted_content():
    parts = split_body("Hi Ken,\n\nSee you Monday.\n")
    assert parts.body == "Hi Ken,\n\nSee you Monday."
    assert parts.forwarded_content is None
    assert parts.quoted_content is None


@pytest.mark.parametrize("marker", [
    "-----Original Message-----",
    "----- Original Message -----",
    "---------------------- Forwarded by Mark Haedicke/HOU/ECT on 05/02/2001 03:08 PM",
    "---------- Forwarded message ----------",
    "Begin forwarded message:",
    "From: Jeff Dasovich on 05/14/2001 10:22 AM",
])
def test_embedded_message_markers(marker):
    parts = split_body(f"My comment.\n\n{marker}\nFrom: someone\nOld text\n")
    assert parts.body == "My comment."
    assert parts.forwarded_content.startswith(marker.strip())
    assert "Old text" in parts.forwarded_content


def test_nested_forwards_stay_in_one_block():
    text = (
        "FYI\n-----Original Message-----\nFrom: a\nfirst\n"
        "---------------------- Forwarded by B/HOU/ECT on 01/02/2001 08:00 AM\nsecond\n"
    )
    parts = split_body(text)
    assert parts.body == "FYI"
    assert "first" in parts.forwarded_content and "second" in parts.forwarded_content


def test_pure_forward_has_empty_body():
    parts = split_body("-----Original Message-----\nFrom: a\ntext\n")
    assert parts.body == ""
    assert parts.forwarded_content is not None


def test_quoted_lines_and_attribution_are_separated():
    text = "Agreed.\n\nOn Mon, May 14, 2001, Jeff wrote:\n> Shall we?\n>> Earlier\nThanks\n"
    parts = split_body(text)
    assert parts.body == "Agreed.\n\nThanks"
    assert parts.quoted_content == "On Mon, May 14, 2001, Jeff wrote:\n> Shall we?\n>> Earlier"


def test_quoted_lines_inside_forwarded_block_stay_there():
    parts = split_body("Note\n-----Original Message-----\n> inner quote\n")
    assert parts.quoted_content is None
    assert "> inner quote" in parts.forwarded_content


# --- extract_headings ------------------------------------------------------------

def test_all_caps_and_numbered_section_headings():
    text = (
        "UN WIRE\n\nHEALTH\nMalaria cases fall.\n\n"
        "3. Globalization of Commodity Trading\nSome text.\n\n"
        "IV. Wrap Up\n\nA. The California Wholesale Markets\n"
    )
    assert extract_headings(text) == [
        "UN WIRE", "HEALTH", "3. Globalization of Commodity Trading",
        "IV. Wrap Up", "A. The California Wholesale Markets",
    ]


def test_underlined_and_decorated_headings():
    text = "Intro.\nQuarterly Results\n=================\nbody\n\n++++CONFIDENTIALITY NOTICE++++\n"
    assert extract_headings(text) == ["Quarterly Results", "CONFIDENTIALITY NOTICE"]


@pytest.mark.parametrize("line", [
    "Dear Mr. Lay:",        # greeting
    "Ken:",                 # salutation
    "FYI",                  # stop word
    "[IMAGE]",              # placeholder
    "Thank you.",           # sentence
    "RE: SAVE THE DATE",    # subject label
    "TITLE:",               # field label
    "Shipping: $12.65",     # label: value
    "SEE WWW.ENRON.COM",    # URL
    "08:03AM EDT SALOMON",  # timestamped wire line
    "J. D. Birdwhistell",   # initials, not a lettered section
    "4. Board members agreed to determine if their institutions",  # wrapped sentence
])
def test_non_headings_are_rejected(line):
    assert extract_headings(f"\n{line}\n\n") == []


def test_wrapped_forward_marker_is_not_an_underlined_heading():
    text = "--------- Forwarded by David Delainey/HOU/ECT on 11/03/2000\n08:32 AM ---------\n"
    assert extract_headings(text) == []


def test_all_caps_email_does_not_count_capitalised_lines():
    assert extract_headings("PLEASE CALL ME\n\nTHE DEAL IS OFF\n\nTHANKS JOHN") == []


def test_repeated_headings_are_kept_once():
    assert extract_headings("\nSUMMARY\n\nx\n\nSUMMARY\n") == ["SUMMARY"]


# --- mentions_attachment -----------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Please review.\n\n - Budget 2001.xls\n", True),
    ("See attached <<Term Sheet.doc>> thanks", True),
    ("Card attached\n - Aronoff, David (E-mail).vcf", True),
    ("Agenda:\n - review budget\n - next steps\n", False),
    ("I will attach the file tomorrow.", False),
])
def test_mentions_attachment(text, expected):
    assert mentions_attachment(text) is expected
