import pytest

from nsf_coa.names import format_nsf, given_compatible, parse_name, same_person_name


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Tyson L. Swetnam", "Swetnam, Tyson L"),
        ("Swetnam, Tyson Lee", "Swetnam, Tyson L"),
        ("Swetnam, T.L.", "Swetnam, T L"),
        ("BALCH, JENNIFER", "Balch, Jennifer"),
        ("Nagy, R. Chelsea", "Nagy, R Chelsea"),
        ("Ludwig van Beethoven", "van Beethoven, Ludwig"),
        ("Carla de la Cruz", "de la Cruz, Carla"),
        ("Smith, Jr., John", "Smith, John"),
        ("John Smith III", "Smith, John"),
        ("Madonna", "Madonna"),
    ],
)
def test_format_nsf(raw, expected):
    assert format_nsf(*parse_name(raw)) == expected


def test_structured_names_win():
    assert parse_name("ignored", "Ana María", "García López") == ("Ana María", "García López")


def test_whole_name_in_family_field_is_reparsed():
    # DataCite sometimes puts the full name in familyName with no givenName.
    assert parse_name("Mary Rose Posa", None, "Mary Rose Posa") == ("Mary Rose", "Posa")


@pytest.mark.parametrize(
    "a, b, ok",
    [
        ("John", "J", True),
        ("John R", "J. R.", True),
        ("John", "Jane", False),
        ("J R", "J K", False),
        ("", "Anything", True),
    ],
)
def test_given_compatible(a, b, ok):
    assert given_compatible(a, b) is ok


def test_same_person_ignores_accents_and_case():
    assert same_person_name("José", "Núñez", "Jose", "NUNEZ")
