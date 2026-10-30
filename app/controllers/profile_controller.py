"""Current-account profile page and personal-field update contract."""
from flask import current_app, jsonify, render_template, request, url_for
from flask_login import current_user
from app.services import profile_service, login_event_service, session_service


def page():
    ref = current_app.debug and request.args.get("ref") == "1"
    tab = request.args.get("tab", "profile")
    if tab not in ("profile", "preferences", "activity", "sessions"):
        tab = "profile"
    activity = {"items": [], "page": 1, "pages": 0, "total": 0, "available": True}
    sessions = {"items": [], "available": True}
    filters = {key: request.args.get(key, "")[:120] for key in ("search", "type", "status", "date_from", "date_to")}
    per_page = request.args.get("per_page", 10, type=int)
    per_page = per_page if per_page in (5, 10, 20) else 10
    if not ref and tab == "activity":
        activity = login_event_service.list_events(current_user, request.args.get("page", 1, type=int), per_page, filters)
    activity["per_page"] = per_page
    activity["first"] = (activity["page"] - 1) * per_page + 1 if activity["items"] else 0
    activity["last"] = (activity["page"] - 1) * per_page + len(activity["items"]) if activity["items"] else 0
    activity["window"] = range(max(1, activity["page"] - 2), min(activity["pages"], activity["page"] + 2) + 1)
    def activity_url(page):
        return url_for("pages.profile", tab="activity", page=page, per_page=per_page, **filters, **({"ref": 1} if ref else {}))
    if not ref and tab == "sessions":
        sessions = session_service.list_sessions(current_user)
    return render_template("profile.html", profile=profile_service.reference() if ref else profile_service.view(current_user),
                           reference_mode=ref, pro_shell=True, movements_live=not ref,
                           tab=tab, activity=activity, activity_filters=filters, activity_url=activity_url, sessions=sessions)


def update():
    return jsonify(message="Perfil actualizado correctamente.", profile=profile_service.update(current_user, request.get_json(silent=True)))
