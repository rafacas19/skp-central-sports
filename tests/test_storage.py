from scouting_bot.models import HOME, Observation, Player
from scouting_bot.storage import _now


def test_one_active_session_per_agent(service):
    s1, existing = service.start_session(111, "A", "B", None)
    assert s1 is not None and existing is None

    s2, existing = service.start_session(111, "C", "D", None)
    assert s2 is None and existing is not None
    assert existing.id == s1.id


def test_session_survives_reload(storage):
    sess = storage.create_session(222, "A", "B", "Liga")
    # Simulate "restart": fetch fresh from disk.
    again = storage.get_active_session(222)
    assert again is not None
    assert again.id == sess.id
    assert again.home_team == "A"


def test_roster_replace_and_list(storage):
    sess = storage.create_session(1, "A", "B", None)
    players = [
        Player(None, sess.id, HOME, 8, "Vidal", "CM"),
        Player(None, sess.id, "away", 8, "Mendes", "CM"),
    ]
    storage.replace_roster(sess.id, players)
    listed = storage.list_players(sess.id)
    assert {p.name for p in listed} == {"Vidal", "Mendes"}


def test_observation_crud(storage):
    sess = storage.create_session(1, "A", "B", None)
    obs = storage.add_observation(
        Observation(None, sess.id, None, HOME, "positive", "passing", "great pass", _now())
    )
    assert obs.id is not None
    last = storage.last_observation(sess.id)
    assert last.raw_quote == "great pass"
    storage.update_observation(obs.id, sentiment="negative")
    assert storage.last_observation(sess.id).sentiment == "negative"
    storage.delete_observation(obs.id)
    assert storage.last_observation(sess.id) is None


def test_stale_sessions_detection(storage):
    storage.create_session(1, "A", "B", None)
    future = "2999-01-01T00:00:00+00:00"
    assert len(storage.stale_active_sessions(future)) == 1
    past = "2000-01-01T00:00:00+00:00"
    assert storage.stale_active_sessions(past) == []
