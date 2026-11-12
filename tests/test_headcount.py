"""The AI's careful count of the people in a photo, with how sure it is (headcount.py): worked out from the chances
Ollama gives the answer's digits, asked of the photos whose faces don't add up, and what Cleanup's lists of faces
missed and a photo's page make of it."""
import dataclasses
import json
import math

import httpx
import pytest
from fastapi.testclient import TestClient

from lineagebox import faces, headcount, llm, people, progress, retry
from lineagebox.web import app as webapp
from tests.conftest import LAN
from tests.test_ai_services import mock_ollama
from tests.test_face_checks import A, face, like, photo


def tok(token, p, *others):
    """One token of an answer as Ollama gives it: its chance, and those of others it could have written there (plus
    a last, unlikely one, as Ollama lists twenty)."""
    listed = [(token, p), *others, ("zzz", 1e-6)]
    return {"token": token, "logprob": math.log(p),
            "top_logprobs": [{"token": t, "logprob": math.log(q)} for t, q in listed]}


def answer(people_digits, faces_digits):
    """The tokens of {"people": .., "faces": ..}, each number's digits given as [(digit, chance, others...)]."""
    out = [tok('{"', 0.9), tok("people", 0.9), tok('":', 0.9), tok(" ", 0.9)]
    out += [tok(*d) for d in people_digits]
    out += [tok(",", 0.95, ("0", 0.05)), tok(' "', 0.9), tok("faces", 0.9), tok('":', 0.9), tok(" ", 0.9)]
    out += [tok(*d) for d in faces_digits]
    return out + [tok("}", 0.98, ("5", 0.02))]


def test_how_sure_the_model_was():
    # 12 people: "1" against "2" (a word it would rather have written doesn't count), "2" against ending there or
    # "5", then ending (the comma) against going on (a "0")
    tokens = answer([("1", 0.6, ("2", 0.2), (" Approximately", 0.15)), ("2", 0.5, ("\n", 0.25), ("5", 0.25))],
                    [("0", 0.9, ("1", 0.1))])
    assert headcount.sure_of(tokens, "people", 12) == pytest.approx(0.6 / 0.8 * 0.5 / 1.0 * 0.95, abs=0.002)
    assert headcount.sure_of(tokens, "faces", 0) == pytest.approx(0.9 * 0.98, abs=0.002)
    assert headcount.sure_of(tokens, "people", 13) is None  # not the number the tokens spell
    assert headcount.sure_of([], "people", 12) is None  # an Ollama that gives no chances
    # a digit barely chosen, with the others not listed: each counted at the least listed chance, so it isn't sure
    barely = [tok('{"people": ', 0.9), {"token": "3", "logprob": math.log(0.01), "top_logprobs": [
        {"token": t, "logprob": math.log(q)} for t, q in ((" about", 0.5), (" around", 0.3), ("3", 0.01),
                                                          ("several", 0.01))]}, tok("}", 0.99)]
    assert headcount.sure_of(barely, "people", 3) < 0.2


def test_ollama_is_asked_for_the_chances(cfg):
    sent = []

    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        sent.append(body := json.loads(request.content))
        tokens = {"logprobs": [{"token": "2", "logprob": -0.1, "top_logprobs": []}]} if body.get("logprobs") else {}
        return httpx.Response(200, json={"message": {"content": '{"people": 2, "faces": 1}'}, "done_reason": "stop",
                                         **tokens})

    o = mock_ollama(cfg, handler)
    found, usage = o.chat_json("system", "user", headcount.SCHEMA, logprobs=20)
    assert found == {"people": 2, "faces": 1} and usage.tokens[0]["token"] == "2"
    assert sent[0]["logprobs"] is True and sent[0]["top_logprobs"] == 20
    assert o.chat_json("system", "user", {})[1].tokens == [] and "logprobs" not in sent[1]  # not asked: as before


class FakeAI:
    """Ollama counting: what it answers for each photo (by the preview it is sent), and which it was asked about."""
    model = "m"

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def chat_json(self, system, user, schema, images=(), temperature=0.1, max_tokens=1024, logprobs=0):
        image_id = int(images[0].decode(), 16)
        self.asked.append(image_id)
        found = self.answers[image_id]
        if isinstance(found, Exception):
            raise found
        n, p, f, q = found
        return {"people": n, "faces": f}, llm.Usage(0, 0, 0.0, answer([(str(n), p, ("9", 1 - p))],
                                                                      [(str(f), q, ("9", 1 - q))]))


def described(con, image_id, people_count):
    con.execute("""INSERT INTO analysis(image_id, model, prompt_version, created_at, caption, people_count, raw_json)
                   VALUES (?, 'm', 1, '2026-11-07T10:00:00', 'People', ?, '{}')""", (image_id, people_count))
    con.execute("UPDATE image SET stage = stage | ? WHERE id = ?", (faces.STAGE_FACES, image_id))


def test_the_photos_counted_and_the_lists_they_are_on(con, cfg, monkeypatch):
    monkeypatch.setattr(headcount, "_picture", lambda cfg_, sha256: sha256.encode())
    ids = {name: photo(con, n, f"20{10 + n}-07-04T12:00:00") for n, name in enumerate(
        ("baby", "hand", "crowd", "group", "all found", "nobody", "broken", "looked at"), start=1)}
    for name, count in (("baby", 2), ("hand", 1), ("crowd", 1_000_000_000_000_000), ("group", 6), ("all found", 2),
                        ("nobody", 0), ("broken", 3), ("looked at", 2)):
        described(con, ids[name], count)
    owen = people.person_id(con, "Owen Miller")
    face(con, ids["group"], like(A), owen, "user")
    face(con, ids["all found"], like(A), owen, "user")
    face(con, ids["all found"], like(A))
    con.execute("UPDATE image SET faces_again = '2026-11-11T10:00:00' WHERE id = ?", (ids["looked at"],))
    con.commit()
    ai = FakeAI({ids["baby"]: (2, 0.95, 1, 0.5),     # two people, sure; a face, unsure: listed
                 ids["hand"]: (1, 0.95, 0, 0.95),    # a hand holding a wire: sure no face shows, not listed
                 ids["crowd"]: (7, 0.3, 4, 0.3),     # a concert: unsure, not listed
                 ids["group"]: (5, 0.9, 4, 0.4),     # five people, one face found: Faces missed
                 ids["broken"]: ValueError("not JSON")})

    # asked about: the photos whose description counts more people than faces were found, not yet looked at closely
    assert headcount.waiting(con, "m") == [ids["broken"], ids["group"], ids["crowd"], ids["hand"], ids["baby"]]
    assert headcount.count_waiting(con, cfg, ai, stop=lambda: True) == 0 and ai.asked == []
    assert headcount.count_waiting(con, cfg, ai) == 4
    assert sorted(ai.asked) == sorted(ids[n] for n in ("baby", "hand", "crowd", "group", "broken"))
    row = con.execute("SELECT * FROM headcount WHERE image_id = ?", (ids["baby"],)).fetchone()
    assert (row["people"], row["faces"], row["model"]) == (2, 1, "m") and row["people_sure"] > 0.85
    assert retry.summary(con)[0]["stage"] == headcount.STAGE  # the one it couldn't count: tried again later
    assert headcount.waiting(con, "m") == []  # each asked once
    assert headcount.waiting(con, "another model") == [ids["group"], ids["crowd"], ids["hand"], ids["baby"]]

    rows, total = faces.no_faces(con)
    assert total == 1 and rows[0]["id"] == ids["baby"] and rows[0]["people"] == 2 and 85 <= rows[0]["sure"] <= 99
    rows, total = faces.missed(con)
    assert total == 1 and (rows[0]["id"], rows[0]["people"], rows[0]["found"]) == (ids["group"], 5, 1)

    client = TestClient(webapp.create_app(cfg), client=LAN)
    page = client.get(f"/image/{ids['baby']}").text
    assert f"2 ({rows and faces.no_faces(con)[0][0]['sure']}% sure)" in page
    page = client.get(f"/image/{ids['broken']}").text  # not counted yet: the description's count, unsure
    assert "<dt>People</dt><dd>3</dd>" in page
    con.execute("DELETE FROM headcount WHERE image_id = ?", (ids["crowd"],))
    con.commit()
    page = client.get(f"/image/{ids['crowd']}").text  # a description's count past believing isn't shown
    assert "<dt>People</dt>" not in page and "1,000,000,000,000,000" not in page
    # the crowd waits to be counted again (the broken one waits for its retry)
    assert "The AI has 1 more photo to count" in client.get("/cleanup/no-faces").text
    status = {r["key"]: r for r in progress.counts(con, dataclasses.replace(cfg, ollama_model="m"))["waiting"]}
    assert status["headcount"]["left"] == 1


def test_ollama_away_ends_the_pass(con, cfg, monkeypatch):
    monkeypatch.setattr(headcount, "_picture", lambda cfg_, sha256: sha256.encode())
    for n in (1, 2):
        described(con, photo(con, n, f"201{n}-01-01T10:00:00"), 2)
    con.commit()
    ai = FakeAI({2: httpx.ConnectError("asleep"), 1: (2, 0.9, 2, 0.9)})
    assert headcount.count_waiting(con, cfg, ai) == 0 and ai.asked == [2]  # the newest first, then it stopped
    assert retry.summary(con) == []  # not held against the photo
