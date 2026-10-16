"""ESPN football lineup slot IDs used by the unofficial fantasy API."""

from __future__ import annotations

SLOT_ID_TO_NAME = {
    0: "QB",
    1: "TQB",
    2: "RB",
    3: "RB/WR",
    4: "WR",
    5: "WR/TE",
    6: "TE",
    7: "OP",
    8: "DT",
    9: "DE",
    10: "LB",
    11: "DL",
    12: "CB",
    13: "S",
    14: "DB",
    15: "DP",
    16: "D/ST",
    17: "K",
    18: "P",
    19: "HC",
    20: "BE",
    21: "IR",
    23: "FLEX",
}

NAME_TO_SLOT_ID = {name: slot_id for slot_id, name in SLOT_ID_TO_NAME.items()}
NAME_TO_SLOT_ID["RB/WR/TE"] = 23
NAME_TO_SLOT_ID["BENCH"] = 20

STARTER_SLOTS = {0, 2, 4, 6, 16, 17, 23, 3, 5, 7}
BENCH_SLOT = 20
IR_SLOT = 21

ELIGIBLE_FOR_SLOT = {
    0: {"QB"},
    2: {"RB"},
    4: {"WR"},
    6: {"TE"},
    16: {"D/ST", "DST", "DEF"},
    17: {"K"},
    23: {"RB", "WR", "TE"},
    3: {"RB", "WR"},
    5: {"WR", "TE"},
    7: {"QB", "RB", "WR", "TE"},
    20: None,
    21: None,
}


def slot_name(slot_id: int | None) -> str:
    if slot_id is None:
        return "UNK"
    return SLOT_ID_TO_NAME.get(int(slot_id), f"SLOT_{slot_id}")


def parse_slot(value: str | int) -> int:
    if isinstance(value, int):
        return value
    raw = str(value).strip().upper()
    if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
        return int(raw)
    if raw in NAME_TO_SLOT_ID:
        return NAME_TO_SLOT_ID[raw]
    raise ValueError(f"Unknown lineup slot: {value!r}")


def player_fits_slot(position: str, slot_id: int) -> bool:
    allowed = ELIGIBLE_FOR_SLOT.get(slot_id)
    if allowed is None:
        return True
    return normalize_position(position) in allowed


def normalize_position(position: str) -> str:
    pos = (position or "").strip().upper()
    if pos in {"DST", "DEF", "D/ST"}:
        return "D/ST"
    return pos


def is_starter_slot(slot_id: int | None) -> bool:
    return slot_id is not None and int(slot_id) in STARTER_SLOTS


def slot_eligibility_size(slot_id: int | None) -> int:
    """How many positions a slot accepts. Bench / IR / unknown accept anything.

    Fill order matters for a greedy lineup: slots must be filled most
    restrictive first. Eligibility here is nested (anyone who fits a WR slot
    also fits FLEX), so filling the exclusive slots before the flexible ones is
    optimal -- there is never a reason to hold a player back from a slot only
    they can fill.
    """
    if slot_id is None:
        return len(ELIGIBLE_FOR_SLOT) + 1
    allowed = ELIGIBLE_FOR_SLOT.get(int(slot_id), None)
    if allowed is None:
        return len(ELIGIBLE_FOR_SLOT) + 1
    return len(allowed)
