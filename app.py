from __future__ import annotations

import json
import os
import secrets
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from random import Random
from typing import Any

from flask import Flask, Response, abort, jsonify, render_template, request, session
from psycopg import IntegrityError

# Custom imports
from feed import assign_feed, plan_probes, select_rq2_versions, CLASS_LABELS, CLIPS_PER_LABEL
from db import get_db, init_db, close_db

BASE_DIR = Path(__file__).resolve().parent
STIMULI_PATH = BASE_DIR / "stimuli_from_catalog.json"
RQ2_STIMULI_PATH = BASE_DIR / "stimuli_from_rq2.json"

app = Flask(__name__)
# Set STUDY_SECRET_KEY in Render before deploying.
app.config["SECRET_KEY"] = os.environ.get("STUDY_SECRET_KEY", "local-prototype-only-change-me")
app.teardown_appcontext(close_db)

def count_rq1_transitions(order):
    """Count transitions between consecutive RQ1 video labels in the given order."""
    # obtain labels associated w/ videos, e.g. ["L", "T", "L", ...]
    labels = [
        None if video.get("category") == "RQ2"
        else CLASS_LABELS.get(video.get("payoff_label"))
        for video in order
    ]

    # check if the number of labels matches the expected count per label (e.g. 6 for "L", 6 for "T", etc.)
    if Counter(label for label in labels if label is not None) != CLIPS_PER_LABEL:
        return Counter()

    # return a counter of transitions between consecutive labels
    return Counter(
        (left, right)
        for left, right in zip(labels, labels[1:])
        if left is not None and right is not None
    )


def build_session_feed(rng):
    catalog = load_stimuli()
    with RQ2_STIMULI_PATH.open(encoding="utf-8") as stream:
        rq2_stimuli = select_rq2_versions(json.load(stream)["videos"], rng)

    stimuli = assign_feed(catalog, rng, rq2_stimuli=rq2_stimuli)

    rq1_stimuli = [video for video in stimuli if video.get("category") != "RQ2"]
    num_free_swipe = len(rq1_stimuli) // 2
    num_fixed_probe = len(rq1_stimuli) - num_free_swipe
    interaction_modes = ["free_swipe"] * num_free_swipe + ["fixed_probe"] * num_fixed_probe
    rng.shuffle(interaction_modes)

    # randomly assign interaction modes to RQ1 stimuli
    for stimulus, interaction_mode in zip(rq1_stimuli, interaction_modes):
        stimulus["interaction_mode"] = interaction_mode

    return stimuli


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def load_stimuli() -> list[dict[str, Any]]:
    """Load and validate the study's MP4 metadata before assigning it."""
    with STIMULI_PATH.open(encoding="utf-8") as stream:
        videos = json.load(stream)["videos"]
    return videos

def ensure_session() -> str:
    """Create a browser-local anonymous study session on its first visit."""
    if "participant_id" in session:
        return session["participant_id"]

    participant_id = secrets.token_urlsafe(16)
    rng = Random(secrets.randbits(64))
    db = get_db()
    stimuli = build_session_feed(rng)

    # adding to manage participant session and trial order
    db.execute(
        "INSERT INTO sessions (id, trial_order_json, started_at) VALUES (%s, %s, %s)",
        (participant_id, json.dumps(stimuli), utc_now()),
    )
    db.commit()
    session["participant_id"] = participant_id
    return participant_id

def current_trial(participant_id: str) -> dict[str, Any] | None:
    db = get_db()

    # fetch the study session for the current participant
    study_session = db.execute(
        "SELECT * FROM sessions WHERE id = %s", (participant_id,)
    ).fetchone()
    if study_session is None:
        abort(404)

    order = json.loads(study_session["trial_order_json"])
    position = study_session["current_position"]
    # if the participant has completed all trials, return None
    if position >= len(order):
        return None

    # check if a trial already exists for the current position
    trial = db.execute(
        "SELECT * FROM trials WHERE session_id = %s AND position = %s",
        (participant_id, position),
    ).fetchone()
    if trial is not None:
        return trial

    # create a new trial for the current stimulus if it doesn't exist
    stimulus = order[position]
    probes = plan_probes(stimulus)

    # insert a new trial into the database for the current stimulus w/ its probe plan
    trial = db.execute(
        """
        INSERT INTO trials
          (session_id, position, video_id, video_title, video_url, interaction_mode,
           probe_plan_json, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            participant_id,
            position,
            stimulus["id"],
            stimulus["title"],
            stimulus["video_url"],
            stimulus["interaction_mode"],
            json.dumps(probes),
            utc_now(),
        ),
    ).fetchone()
    db.commit()
    return trial

def owned_trial(trial_id: int) -> dict[str, Any]:
    """Retrieve a trial only when it belongs to the current participant."""
    participant_id = ensure_session()
    trial = get_db().execute(
        "SELECT * FROM trials WHERE id = %s AND session_id = %s",
        (trial_id, participant_id),
    ).fetchone()
    if trial is None:
        abort(404)
    return trial

@app.cli.command("init-db")
def init_db_command() -> None:
    """Create the study schema in a fresh database."""
    init_db()

@app.get("/")
def study() -> str:
    ensure_session()
    return render_template("study.html")

@app.get("/api/trial/current")
def get_current_trial():
    participant_id = ensure_session()
    # retrieve the current trial for the participant (e.g. the video they are currently on), if any
    trial = current_trial(participant_id)
    if trial is None:
        return jsonify({"complete": True})

    probes = json.loads(trial["probe_plan_json"])

    # read the next probe index for the current trial from the responses table
    row = get_db().execute(
        """
        SELECT probe_index, choice
        FROM responses
        WHERE trial_id = %s
        ORDER BY probe_index DESC
        LIMIT 1
        """,
        (trial["id"],),
    ).fetchone()
    # return the current trial details along with the next probe index for the participant
    return jsonify(
        {
            "complete": False,
            "trial_id": trial["id"],
            "title": trial["video_title"],
            "video_url": trial["video_url"],
            "interaction_mode": trial["interaction_mode"],
            "probes": probes,
            "next_probe_index": row["probe_index"] + 1 if row else 0,
            "last_probe_choice": row["choice"] if row else None,
        }
    )

@app.post("/api/event")
def log_event():
    data = request.get_json(force=True)
    trial = owned_trial(int(data["trial_id"]))
    event_type = str(data["event_type"])
    allowed = {
        "video_started",
        "probe_shown",
        "response_submitted",
        "video_ended",
        "voluntary_swipe",
        "player_error",
    }
    if event_type not in allowed:
        return jsonify({"error": "Unknown event type"}), 400

    db = get_db()
    db.execute(
        """
        INSERT INTO events (trial_id, event_type, video_time, client_wall_time,
                            server_received_at, details_json)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            trial["id"],
            event_type,
            data.get("video_time"),
            data.get("client_wall_time"),
            utc_now(),
            json.dumps(data.get("details", {})),
        ),
    )
    db.commit()
    return jsonify({"ok": True})

@app.post("/api/response")
def save_response():
    data = request.get_json(force=True)
    trial = owned_trial(int(data["trial_id"]))
    probe_index = data.get("probe_index")
    choice = data.get("choice") # participant's choice for the current probe (e.g., "continue" or "next")

    if (
    not isinstance(probe_index, int)
    or choice not in {"continue", "next"}
    ):
        return jsonify({"error": "Invalid response"}), 400

    probes = json.loads(trial["probe_plan_json"])
    if probe_index < 0 or probe_index >= len(probes):
        return jsonify({"error": "Invalid probe index"}), 400

    probe = probes[probe_index]
    db = get_db()

    # record the participant's response to the current probe
    try:
        db.execute(
            """
            INSERT INTO responses
              (trial_id, probe_index, planned_probe_time, probe_category,
               choice, submitted_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (trial_id, probe_index) DO NOTHING
            """,
            (
                trial["id"],
                probe_index,
                probe["time"],
                probe["category"],
                choice,
                utc_now(),
            ),
        )
        saved = db.execute(
            "SELECT choice FROM responses WHERE trial_id = %s AND probe_index = %s",
            (trial["id"], probe_index),
        ).fetchone()
        db.commit()
    except IntegrityError:
        db.rollback()
        app.logger.exception("Could not save probe response")
        return jsonify({"error": "Could not save probe response"}), 500

    return jsonify({"ok": True, "choice": saved["choice"]})

@app.post("/api/trial/next")
def advance_trial():
    data = request.get_json(force=True)
    trial = owned_trial(int(data["trial_id"]))
    db = get_db()
    db.execute("UPDATE trials SET ended_at = %s WHERE id = %s", (utc_now(), trial["id"]))
    db.execute(
        "UPDATE sessions SET current_position = current_position + 1 WHERE id = %s",
        (ensure_session(),),
    )
    db.commit()
    return jsonify({"ok": True})

@app.get("/admin/summary")
def admin_summary():
    """Password-protected inspection endpoint for the study owner."""
    admin_password = os.environ.get("ADMIN_PASSWORD")
    auth = request.authorization
    if (
        not admin_password
        or auth is None
        or auth.username != "admin"
        or not secrets.compare_digest(auth.password or "", admin_password)
    ):
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Study results"'},
        )

    # obtaining the relevant response rows for all trials
    response_rows = get_db().execute(
        """
        SELECT
            t.session_id,
            t.id AS trial_id,
            t.video_id,
            t.position,
            t.video_title,
            r.probe_index,
            r.planned_probe_time,
            r.probe_category,
            r.choice,
            r.submitted_at
        FROM trials AS t
        LEFT JOIN responses AS r ON r.trial_id = t.id
        ORDER BY t.id DESC, r.probe_index ASC
        """
    ).fetchall()

    # obtaining the relevant voluntary swipe events for all trials
    voluntary_swipes = get_db().execute(
        """
        SELECT
            t.session_id,
            t.id AS trial_id,
            t.video_id,
            t.position,
            t.video_title,
            t.interaction_mode,
            e.video_time AS swipe_time_seconds,
            e.client_wall_time AS swiped_at,
            e.details_json
        FROM events AS e
        JOIN trials AS t ON t.id = e.trial_id
        WHERE e.event_type = 'voluntary_swipe'
        ORDER BY e.id ASC
        """
    ).fetchall()

    for swipe in voluntary_swipes:
        swipe["details"] = json.loads(swipe.pop("details_json") or "{}")

    session_rows = get_db().execute(
        """
        SELECT id AS session_id, trial_order_json
        FROM sessions
        ORDER BY started_at DESC, id
        """
    ).fetchall()

    video_orders = {
        row["session_id"]: json.loads(row["trial_order_json"])
        for row in session_rows
    }

    def is_rq2_trial(row):
        stimulus = video_orders[row["session_id"]][row["position"]]
        return stimulus.get("category") == "RQ2"

    return jsonify({
        "responses": [row for row in response_rows if not is_rq2_trial(row)],
        "voluntary_swipes": [row for row in voluntary_swipes if not is_rq2_trial(row)],
        "rq2_voluntary_swipes": [row for row in voluntary_swipes if is_rq2_trial(row)],
        "sessions": [
            {"session_id": session_id, "video_order": order}
            for session_id, order in video_orders.items()
        ],
    })

if __name__ == "__main__":
    app.run(debug=True)
