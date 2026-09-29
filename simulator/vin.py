"""
ISO 3779 Compliant Vehicle Identification Number (VIN) Generator and Validator.
Ensures 17-character length, exclusion of letters I, O, Q, and valid position-9 check digits.
"""

from typing import Set

VIN_TRANSLITERATION = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
}

VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
FORBIDDEN_LETTERS = {"I", "O", "Q"}
ALLOWED_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"


def calculate_check_digit(vin_chars_17: list) -> str:
    """Calculates ISO 3779 check digit for a 17-character VIN array."""
    total = 0
    for i in range(17):
        if i == 8:
            continue
        c = vin_chars_17[i]
        val = VIN_TRANSLITERATION[c]
        total += val * VIN_WEIGHTS[i]
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def validate_vin(vin: str) -> bool:
    """Validates that a VIN is exactly 17 characters, has no I/O/Q, and has a correct check digit."""
    if not isinstance(vin, str) or len(vin) != 17:
        return False
    vin_upper = vin.upper()
    if any(c in FORBIDDEN_LETTERS for c in vin_upper):
        return False
    if not all(c in VIN_TRANSLITERATION for c in vin_upper):
        return False
    expected_check = calculate_check_digit(list(vin_upper))
    return vin_upper[8] == expected_check


def generate_vin(
    rng,
    serial: int,
    wmi: str = "1HG",
    vds: str = "CM826",
    model_year_char: str = "M",
    plant_code: str = "A",
) -> str:
    """
    Generates a unique, valid 17-character VIN.
    wmi: 3 chars
    vds: 5 chars
    pos 9: check digit (computed)
    pos 10: model year
    pos 11: plant
    pos 12-17: 6-digit serial
    """
    serial_str = f"{serial % 1000000:06d}"
    chars = list(wmi + vds + "_" + model_year_char + plant_code + serial_str)
    check = calculate_check_digit(chars)
    chars[8] = check
    vin = "".join(chars)
    assert validate_vin(vin), f"Generated invalid VIN: {vin}"
    return vin


def generate_unique_vins(rng, count: int, prefix: str = "1HG") -> list:
    """Generates `count` unique and valid VINs."""
    vins = []
    seen: Set[str] = set()
    serial = 1000
    while len(vins) < count:
        # Vary VDS to support large counts
        vds_suffix = f"{len(vins) // 10000:02d}"
        vds = f"FP{vds_suffix}X"[:5]
        vin = generate_vin(rng, serial=serial, wmi=prefix, vds=vds)
        if vin not in seen:
            seen.add(vin)
            vins.append(vin)
        serial += 1
    return vins
