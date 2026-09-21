import re


SYNTHETIC_TITLE_PATTERN = re.compile(
    r"^(?:Higher Score|More Popular|More Episodes|More Recent)\d+$"
)
KNOWN_SYNTHETIC_MAL_IDS = frozenset(
    list(range(991000, 991006))
    + list(range(991010, 991016))
    + list(range(991020, 991026))
    + list(range(991030, 991036))
)


def has_synthetic_fixture_title(title):
    return bool(SYNTHETIC_TITLE_PATTERN.fullmatch(title or ""))


def is_known_synthetic_fixture(mal_id, title=None):
    return (
        mal_id in KNOWN_SYNTHETIC_MAL_IDS
        or has_synthetic_fixture_title(title)
    )


def has_unexpected_synthetic_fixture_title(mal_id, title):
    return (
        has_synthetic_fixture_title(title)
        and mal_id not in KNOWN_SYNTHETIC_MAL_IDS
    )
