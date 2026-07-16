import re

from code.common.text_utils import normalize_text

def extract_address_numbers(value) -> set[str]:
    normalized = normalize_text(value)

    if not normalized:
        return set()

    return set(
        re.findall(
            r"\b\d+\b",
            normalized,
        )
    )


def compare_address_numbers(
    first,
    second,
) -> tuple[bool, bool]:
    first_numbers = extract_address_numbers(first)
    second_numbers = extract_address_numbers(second)

    numbers_available = bool(
        first_numbers and second_numbers
    )

    if not numbers_available:
        return False, False

    same_address_number = bool(
        first_numbers.intersection(second_numbers)
    )

    return True, same_address_number

