import os
import time
from datetime import timezone

from bson import ObjectId
from bson.errors import InvalidId
from flask import Flask, abort, jsonify, request
from flask_cors import CORS
from redis import Redis
from rq import Queue
from rq.command import send_stop_job_command
from rq.job import Job
from werkzeug.exceptions import HTTPException

import jobs
from models.track import DiscoverySource
from services.database_manager import DatabaseManager
from services.search_throttle import SearchThrottle
from services.track_manager import TrackManager


def create_app() -> Flask:
    app = Flask(__name__)

    # The frontend runs on a different origin (different port = different origin
    # to the browser), so without this the browser blocks every response with a
    # "Failed to fetch" even though the backend itself answers fine.
    frontend_origins = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",")
    CORS(app, origins=frontend_origins)

    redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    queue = Queue("mashup_matcher", connection=redis_conn)

    db = DatabaseManager()
    db.ensure_indexes()
    # Redis-backed, so the API reports the same cooldown the worker's jobs are pacing against
    throttle = SearchThrottle(redis_conn)
    tm = TrackManager(db, throttle=throttle)

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        return jsonify(error=exc.description), exc.code

    def to_object_id(raw: str):
        try:
            return ObjectId(raw)
        except InvalidId:
            abort(400, description=f"Invalid id: {raw!r}")

    def as_json(model, **kwargs):
        return jsonify(model.model_dump(mode="json", by_alias=True, **kwargs))

    def idea_dict(idea) -> dict:
        """A mashup idea plus a small summary of each of its tracks, so clients can
        show "Song A x Song B" without a request per track."""
        data = idea.model_dump(mode="json", by_alias=True)
        data["tracks"] = []
        for track_id in idea.track_ids:
            track = tm.get_track(track_id)
            data["tracks"].append(
                {
                    "_id": str(track_id),
                    "title": track.title if track else None,
                    "artist": track.artist if track else None,
                    "consensus": track.consensus.model_dump(mode="json") if track and track.consensus else None,
                }
            )
        return data

    job_kinds = {
        "jobs.run_enrichment": "bulk",
        "jobs.run_enrich_track": "track",
        "jobs.run_recompute": "recompute",
        "jobs.run_wikipedia_sweep": "import",
    }

    def epoch(dt):
        if dt is None:
            return None
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()

    def job_error(job: Job):
        try:
            text = getattr(job.latest_result(), "exc_string", None) or job.exc_info
        except Exception:
            text = None
        return text.strip().splitlines()[-1] if text else None

    # RQ notices a job whose worker died (e.g. restarted mid-job) within a couple of minutes
    # and fails it with a raw AbandonedJobError - explain that in plain words instead
    interrupted_message = (
        "Interrupted: the worker restarted while this job was running. "
        "Start it again - anything it hadn't reached is still pending."
    )

    def job_failure(job: Job):
        error = job_error(job)
        return interrupted_message if error and "AbandonedJobError" in error else error

    def job_summary(job: Job) -> dict:
        """A job's state, live progress (published by the worker into job.meta), and result."""
        status = job.get_status()
        kind = job_kinds.get(job.func_name, job.func_name)
        # RQ's position is 0-based; report 1-based ("#1 in line") so 0 isn't mistaken for "none"
        position = job.get_position() if status == "queued" else None
        data = {
            "job_id": job.id,
            "kind": kind,
            "status": status,
            "progress": job.meta.get("progress"),
            "position": position + 1 if position is not None else None,
            "enqueued_at": epoch(job.enqueued_at),
            "started_at": epoch(job.started_at),
            "ended_at": epoch(job.ended_at),
            "result": job.return_value() if status == "finished" else None,
            "error": job_failure(job) if status == "failed" else None,
        }
        if kind == "track" and job.args:
            track = tm.get_track(ObjectId(job.args[0]))
            data["track"] = {
                "_id": job.args[0],
                "title": track.title if track else None,
                "artist": track.artist if track else None,
            }
        if kind == "bulk":
            data["limit"] = job.kwargs.get("limit")
        if kind == "import":
            data["params"] = {k: job.kwargs.get(k) for k in ("list_names", "year_from", "year_to")}
        return data

    def require_fields(body: dict, *fields: str) -> None:
        missing = [f for f in fields if not body.get(f)]
        if missing:
            abort(400, description=f"Missing required field(s): {', '.join(missing)}")

    # -- Health -----------------------------------------------------------------

    @app.get("/api/health")
    def health():
        mongo_ok = db.ping()
        try:
            redis_ok = redis_conn.ping()
        except Exception:
            redis_ok = False
        status = 200 if mongo_ok and redis_ok else 503
        return jsonify(mongo=mongo_ok, redis=redis_ok), status

    # -- Tracks -------------------------------------------------------------------

    @app.post("/api/tracks")
    def add_track():
        body = request.get_json(silent=True) or {}
        require_fields(body, "title", "artist")

        discovered_from = None
        if body.get("discovered_from"):
            discovered_from = DiscoverySource.model_validate(body["discovered_from"])

        track_id = tm.add_track(body["title"], body["artist"], discovered_from=discovered_from)
        return as_json(tm.get_track(track_id)), 201

    @app.get("/api/tracks")
    def list_tracks():
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
        tracks = tm.list_tracks(
            q=request.args.get("q"),
            status=request.args.get("status"),
            limit=limit,
            offset=offset,
        )
        return jsonify([t.model_dump(mode="json", by_alias=True) for t in tracks])

    @app.get("/api/tracks/<track_id>")
    def get_track(track_id: str):
        track = tm.get_track(to_object_id(track_id))
        if track is None:
            abort(404, description="Track not found")
        return as_json(track)

    @app.get("/api/tracks/<track_id>/matches")
    def get_matches(track_id: str):
        matches = tm.find_compatible(
            to_object_id(track_id),
            bpm_tolerance_pct=float(request.args.get("bpm_tolerance_pct", 6.0)),
            energy_boost=request.args.get("energy_boost", "false").lower() == "true",
            limit=min(int(request.args.get("limit", 25)), 100),
        )
        return jsonify(
            [{"track": t.model_dump(mode="json", by_alias=True), "score": score} for t, score in matches]
        )

    @app.post("/api/tracks/<track_id>/recompute")
    def recompute_track(track_id: str):
        oid = to_object_id(track_id)
        if tm.get_track(oid) is None:
            abort(404, description="Track not found")
        return jsonify(jobs.run_recompute(str(oid)))

    @app.post("/api/tracks/<track_id>/enrich")
    def enrich_track(track_id: str):
        oid = to_object_id(track_id)
        if tm.get_track(oid) is None:
            abort(404, description="Track not found")
        # Generous timeout: the search may first have to wait out a rate-limit cooldown
        job = queue.enqueue(jobs.run_enrich_track, str(oid), job_timeout=int(throttle.backoff_max) + 300)
        return jsonify(job_id=job.id), 202

    # -- Enrichment (background jobs via RQ) ---------------------------------------

    @app.post("/api/enrichment/run")
    def start_enrichment():
        body = request.get_json(silent=True) or {}
        limit = int(body.get("limit", 50))
        # Searches are paced one at a time, so the timeout scales with the batch size, plus
        # room for a full run of doubling backoffs before the run stops itself early
        backoff_budget = sum(
            min(throttle.backoff_base * 2**i, throttle.backoff_max) for i in range(throttle.max_consecutive_blocks)
        )
        job = queue.enqueue(
            jobs.run_enrichment,
            limit=limit,
            max_results=int(body.get("max_results", 5)),
            search_engines=body.get("search_engines"),
            job_timeout=int(limit * (throttle.max_delay + 15) + backoff_budget + 300),
        )
        return jsonify(job_id=job.id), 202

    @app.post("/api/enrichment/recompute")
    def start_recompute_all():
        job = queue.enqueue(jobs.run_recompute, track_id=None, job_timeout="10m")
        return jsonify(job_id=job.id), 202

    @app.get("/api/enrichment/jobs/<job_id>")
    def get_job(job_id: str):
        try:
            job = Job.fetch(job_id, connection=redis_conn)
        except Exception:
            abort(404, description="Job not found")
        return jsonify({**job_summary(job), "server_time": time.time()})

    @app.post("/api/enrichment/jobs/<job_id>/stop")
    def stop_job(job_id: str):
        """Stop a running job, or cancel a queued one. Work already done is kept: tracks that
        were processed keep their results, and anything not reached yet stays pending."""
        try:
            job = Job.fetch(job_id, connection=redis_conn)
        except Exception:
            abort(404, description="Job not found")

        status = job.get_status()
        if status == "started":
            send_stop_job_command(redis_conn, job.id)
        elif status in ("queued", "deferred", "scheduled"):
            job.cancel()
        else:
            abort(409, description=f"Job is already {getattr(status, 'value', status)}")
        return jsonify(job_id=job.id, previous_status=status), 202

    @app.get("/api/enrichment/status")
    def enrichment_status():
        return jsonify(tm.enrichment_status_counts())

    @app.get("/api/enrichment/activity")
    def enrichment_activity():
        """Everything the UI needs to show enrichment live, in one poll: running and queued
        jobs with their progress, recently finished ones, the search throttle's state, and
        track counts by status. `server_time` lets clients count down without clock skew."""
        active_ids = queue.started_job_registry.get_job_ids() + queue.get_job_ids()
        recent_ids = (
            queue.finished_job_registry.get_job_ids()
            + queue.failed_job_registry.get_job_ids()  # includes stopped jobs
            + queue.canceled_job_registry.get_job_ids()
        )

        active = [job_summary(j) for j in Job.fetch_many(active_ids, connection=redis_conn) if j]
        recent = sorted(
            (job_summary(j) for j in Job.fetch_many(recent_ids, connection=redis_conn) if j),
            key=lambda j: j["ended_at"] or j["enqueued_at"] or 0,  # canceled jobs never "end"
            reverse=True,
        )[:5]

        return jsonify(
            active=active,
            recent=recent,
            throttle=throttle.status(),
            counts=tm.enrichment_status_counts(),
            server_time=time.time(),
        )

    # -- Wikipedia import (fast enough to run synchronously) -----------------------

    @app.post("/api/imports/wikipedia")
    def import_wikipedia():
        body = request.get_json(silent=True) or {}
        require_fields(body, "year", "list_name")
        try:
            track_ids = tm.import_from_wikipedia(int(body["year"]), body["list_name"])
        except (ValueError, TypeError) as exc:
            abort(400, description=str(exc))
        return jsonify(imported=len(track_ids), track_ids=[str(i) for i in track_ids])

    @app.post("/api/imports/wikipedia/sweep")
    def import_wikipedia_sweep():
        """Queue an import of several charts across a year range - one page per (chart, year),
        so it runs as a background job with live progress (see /api/enrichment/activity)."""
        body = request.get_json(silent=True) or {}
        require_fields(body, "list_names", "year_from", "year_to")
        try:
            list_names = list(body["list_names"])
            year_from, year_to = int(body["year_from"]), int(body["year_to"])
            tm.validate_wikipedia_sweep(list_names, year_from, year_to)
        except (ValueError, TypeError) as exc:
            abort(400, description=str(exc))

        pages = len(list_names) * (year_to - year_from + 1)
        job = queue.enqueue(
            jobs.run_wikipedia_sweep,
            list_names=list_names,
            year_from=year_from,
            year_to=year_to,
            job_timeout=pages * 30 + 300,
        )
        return jsonify(job_id=job.id, pages=pages), 202

    # -- Mashup ideas ---------------------------------------------------------------

    @app.post("/api/mashup-ideas")
    def add_mashup_idea():
        body = request.get_json(silent=True) or {}
        require_fields(body, "track_id_a", "track_id_b")
        try:
            idea_id = tm.save_idea(
                to_object_id(body["track_id_a"]),
                to_object_id(body["track_id_b"]),
                score=body.get("score"),
                notes=body.get("notes", ""),
            )
        except ValueError as exc:
            abort(400, description=str(exc))
        return jsonify(idea_dict(tm.get_mashup_idea(idea_id))), 201

    @app.get("/api/mashup-ideas")
    def list_mashup_ideas():
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
        ideas = tm.list_mashup_ideas(status=request.args.get("status"), limit=limit, offset=offset)
        return jsonify([idea_dict(i) for i in ideas])

    @app.patch("/api/mashup-ideas/<idea_id>")
    def update_mashup_idea(idea_id: str):
        body = request.get_json(silent=True) or {}
        idea = tm.update_mashup_idea(
            to_object_id(idea_id),
            status=body.get("status"),
            notes=body.get("notes"),
            score=body.get("score"),
        )
        if idea is None:
            abort(404, description="Mashup idea not found")
        return jsonify(idea_dict(idea))

    @app.delete("/api/mashup-ideas/<idea_id>")
    def delete_mashup_idea(idea_id: str):
        if not tm.delete_mashup_idea(to_object_id(idea_id)):
            abort(404, description="Mashup idea not found")
        return "", 204

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
