from league_manager.serialize import player_to_dict


class _Espn46Player:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_player_to_dict_parses_espn_api_46_string_lineup_slot():
    player = _Espn46Player(
        playerId=4374302,
        name="Amon-Ra St. Brown",
        position="WR",
        lineupSlot="WR",
        injured=False,
        projected_points=19.14,
    )
    data = player_to_dict(player)
    assert data["lineup_slot_id"] == 4
    assert data["lineup_slot"] == "WR"
    assert data["is_starter"] is True


def test_player_to_dict_maps_flex_and_bench_slot_names():
    flex = player_to_dict(
        _Espn46Player(playerId=1, name="Flex WR", position="WR", lineupSlot="RB/WR/TE")
    )
    bench = player_to_dict(
        _Espn46Player(playerId=2, name="Bench RB", position="RB", lineupSlot="BE")
    )
    assert flex["lineup_slot_id"] == 23
    assert flex["lineup_slot"] == "FLEX"
    assert flex["is_starter"] is True
    assert bench["lineup_slot_id"] == 20
    assert bench["lineup_slot"] == "BE"
    assert bench["is_starter"] is False


def test_player_to_dict_still_reads_integer_lineup_slot_id():
    data = player_to_dict(
        _Espn46Player(playerId=3, name="Starter RB", position="RB", lineupSlotId=2)
    )
    assert data["lineup_slot_id"] == 2
    assert data["lineup_slot"] == "RB"
    assert data["is_starter"] is True
