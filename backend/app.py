import os

from bson import ObjectId
from bson.errors import InvalidId
from flask import Flask, abort, jsonify, request
from flask_cors import CORS
from redis import Redis
from rq import Queue
from rq.job import Job
from werkzeug.exceptions import HTTPException

import jobs
from models.track import DiscoverySource
from services.database_manager import DatabaseManager
from services.track_manager import TrackManager


def create_app() -> Flask:
    app = Flask(__name__)

    # The frontend runs on a different origin (different port = different origin
    # to the browser), so without this the browser blocks every response with a
    # "Failed to fetch" even though the backend itself answers fine.
    frontend_origins = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",")
    CORS(app, origins=frontend_origins)

    db = DatabaseManager()
    db.ensure_indexes()
    tm = TrackManager(db)

    redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    queue = Queue("mashup_matcher", connection=redis_conn)

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

    # -- Enrichment (background jobs via RQ) ---------------------------------------

    @app.post("/api/enrichment/run")
    def start_enrichment():
        body = request.get_json(silent=True) or {}
        job = queue.enqueue(
            jobs.run_enrichment,
            limit=int(body.get("limit", 50)),
            concurrency=int(body.get("concurrency", 4)),
            max_results=int(body.get("max_results", 5)),
            search_engines=body.get("search_engines"),
            job_timeout="10m",
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
        return jsonify(
            job_id=job.id,
            status=job.get_status(),
            result=job.return_value(),
            error=str(job.exc_info) if job.exc_info else None,
        )

    @app.get("/api/enrichment/status")
    def enrichment_status():
        return jsonify(tm.enrichment_status_counts())

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
        return as_json(tm.get_mashup_idea(idea_id)), 201

    @app.get("/api/mashup-ideas")
    def list_mashup_ideas():
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
        ideas = tm.list_mashup_ideas(status=request.args.get("status"), limit=limit, offset=offset)
        return jsonify([i.model_dump(mode="json", by_alias=True) for i in ideas])

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
        return as_json(idea)

    @app.delete("/api/mashup-ideas/<idea_id>")
    def delete_mashup_idea(idea_id: str):
        if not tm.delete_mashup_idea(to_object_id(idea_id)):
            abort(404, description="Mashup idea not found")
        return "", 204

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
