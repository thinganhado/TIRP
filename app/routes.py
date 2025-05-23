# app/routes.py
from flask import Blueprint, render_template, jsonify
from flask import request, redirect, url_for, flash, session
import json, uuid, os, sys, subprocess
from datetime import datetime
from math import ceil
from app.visualisation.generate_graphs import generate_graphs  # ensure this import is at the top

import pandas as pd
from sqlalchemy import text

from app.database.student_queries import (
    fetch_all_students,
    fetch_student_details,
    fetch_students,
)
from app.database.class_queries import (
    fetch_unique_classes,
    fetch_all_classes,
    fetch_disrespect_for_class,
    get_cohort_averages,
    get_class_metrics,
    fetch_classes_summary,
    fetch_conflict_pairs_per_class,
)

from app.database.friends_queries import build_friendship_graph_json
from app.database.combined_queries import build_combined_graph_json
from app.database.softcons_queries import SoftConstraint
from app.database.spec_endpoint import HardConstraint
from app.models.assistant import AssistantModel
from app import db
from subprocess import run as sh

SPEC_SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__),
                 "ml_models",         
                 "apply_specifications.py")
)
# ──────────────────────────────────────────

assistant = AssistantModel()           # NLP / rule-based helper
main      = Blueprint("main", __name__)

# ╭────────  core pages  ─────────╮
@main.route("/")
def dashboard():
    return render_template("Index.html")

@main.route("/students")
def students():
    return render_template("Students.html")

# ─────────────  classes page  ─────────────
@main.route("/classes")
def classes():
    all_classes = fetch_all_classes()        # [{name, students:[{id,name},…]}]
    cohort_avg_gpa, cohort_avg_well = get_cohort_averages()

    for cls in all_classes:
        # split bully / victim / remainder
        edges       = fetch_disrespect_for_class(cls["name"])
        bully_ids   = {e["target"] for e in edges}
        victim_ids  = {e["source"] for e in edges} - bully_ids
        remainder   = [s for s in cls["students"]
                       if s["id"] not in bully_ids | victim_ids]
        remainder.sort(key=lambda s: s["name"])
        half = ceil(len(remainder) / 2)

        cls["left_students"]  = (
            [s for s in cls["students"] if s["id"] in bully_ids] + remainder[:half]
        )
        cls["right_students"] = (
            [s for s in cls["students"] if s["id"] in victim_ids] + remainder[half:]
        )
        cls["edges"] = edges

        # per-class metrics
        m                  = get_class_metrics(cls["name"])
        cls["avg_gpa"]       = m["avg_gpa"]
        cls["avg_well"]      = m["avg_well"]
        cls["num_conflicts"] = m["num_conflicts"]

    return render_template(
        "Classes.html",
        classes=all_classes,
        cohort_avg_gpa=cohort_avg_gpa,
        cohort_avg_well=cohort_avg_well,
    )

# ╭────────  visualisation pages  ─────────╮
def _safe_recommendations():
    """Return assistant recs or a default list if model fails."""
    try:
        return assistant.get_recommendations()
    except Exception as e:
        print(f"[viz] get_recommendations failed: {e}")
        return [
            "Explore the graphs to understand class dynamics.",
            "Look for isolated students who may need support.",
            "Adjust priorities in the customisation section if needed.",
        ]

@main.route("/visualization/overall")
def overall():
    return render_template("Overall.html", recommendations=_safe_recommendations())

@main.route("/visualization/combined")
def combined_graph():
    graph_data = build_combined_graph_json()
    return render_template("CScombined_embed.html", graph_data=graph_data)

@main.route("/visualization/friendship")
def friendship_graph():
    graph_data = build_friendship_graph_json()
    return render_template("CSfriendship_embed.html", graph_data=graph_data)

@main.route("/visualization/individual")
def individual():
    return render_template("studentindividual.html", recommendations=_safe_recommendations())

@main.route("/visualization/comparison")
def comparison():
    from app.visualisation.generate_graphs import generate_graphs

    # Get chart images
    charts = generate_graphs()

    # Fetch numerical insights from allocation_insights1
    query = text("SELECT * FROM allocation_insights1")
    result = db.session.execute(query).mappings().all()

    # Organize by allocation_type
    insights = {row["allocation_type"]: row for row in result}
    ga = insights["GA"]
    rand = insights["Random"]

    # Generate insight texts using stats
    ga_insights = {
        # Cards next to graphs
        "gpa_fairness": f"The standard deviation of class GPA in the Random allocation is {rand['gpa_std_dev']:.2f}, compared to {ga['gpa_std_dev']:.2f} in the Genetic Algorithm allocation. This indicates that GPA is more evenly distributed under GA, promoting academic balance across classrooms.",
        "high_risk_dynamics": f"The total number of bullying-related conflicts in the Random allocation was {rand['bully_conflict_count']}, compared to {ga['bully_conflict_count']} under GA. This suggests that GA reduces exposure to high-risk social dynamics.",

        # Bottom cards
        "friendship_coverage": f"Students in GA had an average of {ga['avg_friends_per_student']:.2f} friends per class, while Random only achieved {rand['avg_friends_per_student']:.2f}. This implies GA placements foster stronger peer connections.",
        "influence_distribution": f"Random allocation had a higher variation in influential student placement (std dev = {rand['influence_std_dev']:.4f}) than GA (std dev = {ga['influence_std_dev']:.4f}). GA better spreads peer leaders across classes.",
        "isolation_risk": f"Isolation standard deviation under Random was {rand['isolation_std_dev']:.4f}, higher than GA's {ga['isolation_std_dev']:.4f}, meaning GA prevents clustering of socially isolated students.",
    }

    return render_template("comparison.html",
                           gpa_chart_img=charts.get("gpa_chart_img"),
                           conflict_chart_img=charts.get("conflict_chart_img"),
                           wellbeing_chart_img=None,  # placeholder until available
                           ga_insights=ga_insights)

# ╭────────  customisation workflow  ───────╮
@main.route("/customisation")
def customisation_home():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return render_template("customisation.html", session_id=session["session_id"])

@main.route("/customisation/set-priorities")
def set_priorities():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    priority_recs = assistant.get_priority_recommendations()
    return render_template("set_priorities.html",
                           recommendations=priority_recs,
                           session_id=session["session_id"])

@main.route('/customisation/specification')
def specification():
    students   = fetch_students()
    classes    = fetch_classes_summary()
    conflicts  = fetch_conflict_pairs_per_class()     # use pairs
    return render_template('specifications.html',
                           students=students,
                           classes_summary=classes,
                           conflict_by_class=conflicts)

@main.route("/customisation/ai-assistant")
def ai_assistant():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    
    # Check for reset parameter and clear chat history if present
    if request.args.get('reset') == 'true':
        assistant.clear_chat_history(session_id=session["session_id"])
    
    chat_history = assistant.get_chat_history(session_id=session["session_id"])
    return render_template("ai_assistant.html",
                           chat_history=chat_history,
                           session_id=session["session_id"])

@main.route("/customisation/history")
def history():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    all_history = assistant.get_chat_history(limit=50)
    return render_template("history.html",
                           chat_history=all_history,
                           session_id=session["session_id"])

# ---------------- submit_customisation (unchanged) ----------------
@main.route('/submit_customisation', methods=['POST'])
def submit_customisation():
    """
    Handles the "Set Priorities" form.
    We **no longer** run the GA directly here – we just persist the row
    then redirect to `/run_allocation`, the single GA trigger used by both pages.
    """
    try:
        # --- Extract sliders / priorities ----------------------------------
        gpa_penalty_weight       = int(request.form.get("gpa_penalty_weight",        30))
        wellbeing_penalty_weight = int(request.form.get("wellbeing_penalty_weight",  50))
        bully_penalty_weight     = int(request.form.get("bully_penalty_weight",      60))
        influence_std_weight     = int(request.form.get("influence_std_weight",      60))
        isolated_std_weight      = int(request.form.get("isolated_std_weight",       60))
        min_friends_required     = int(request.form.get("min_friends_required",       1))
        friend_inclusion_weight  = int(request.form.get("friend_inclusion_weight",   60))
        friendship_balance_weight= int(request.form.get("friend_balance_weight",     60))

        priority_csv   = request.form.get("priority_order", "")
        priority_list  = priority_csv.split(",") if priority_csv else []

        # --- Map rank-order to columns -------------------------------------
        priority_mapping = {
            "academic_performance":   "prioritize_academic",
            "student_wellbeing":      "prioritize_wellbeing",
            "bullying_prevention":    "prioritize_bullying",
            "social_influence":       "prioritize_social_influence",
            "friendship_connections": "prioritize_friendship",
        }
        priority_weights = {v: 0 for v in priority_mapping.values()}
        for rank, key in enumerate(priority_list[::-1], start=1):
            if key in priority_mapping:
                priority_weights[priority_mapping[key]] = rank

        # --- Persist to DB --------------------------------------------------
        new_entry = SoftConstraint(
            gpa_penalty_weight          = gpa_penalty_weight,
            wellbeing_penalty_weight    = wellbeing_penalty_weight,
            bully_penalty_weight        = bully_penalty_weight,
            influence_std_weight        = influence_std_weight,
            isolated_std_weight         = isolated_std_weight,
            min_friends_required        = min_friends_required,
            friend_inclusion_weight     = friend_inclusion_weight,
            friendship_balance_weight   = friendship_balance_weight,
            **priority_weights
        )
        db.session.add(new_entry)
        db.session.commit()

        # --- Also keep the JSON copy read by GA ----------------------------
        with open("app/ml_models/soft_constraints_config.json", "w") as f:
            json.dump({
                "gpa_penalty_weight"       : gpa_penalty_weight,
                "wellbeing_penalty_weight" : wellbeing_penalty_weight,
                "bully_penalty_weight"     : bully_penalty_weight,
                "influence_std_weight"     : influence_std_weight,
                "isolated_std_weight"      : isolated_std_weight,
                "min_friends_required"     : min_friends_required,
                "friend_inclusion_weight"  : friend_inclusion_weight,
                "friendship_balance_weight": friendship_balance_weight,
                **priority_weights
            }, f, indent=2)

        # --- All done – kick off GA by redirecting to one common route  <<< CHANGED
        return redirect(url_for("main.run_allocation"))

    except Exception as e:
        flash(f"Submission error: {e}")
        return redirect(url_for("main.set_priorities"))

@main.route("/customisation/loading")
def customisation_loading():
    return render_template("customisation_loading.html",
                           from_set_priorities=True)   # keep v1 flag

@main.route("/api/hard_constraints", methods=["POST"])
def post_hard_constraints():
    """
    • insert JSON record
    • call SQL-based spec-applier
    • return JSON {ok:true} / {error:...}
    """
    data = request.get_json(force=True)
    if not {"separate_pairs", "forced_moves"} <= data.keys():
        return jsonify(error="Missing keys"), 400

    rec = HardConstraint(separate_pairs=data["separate_pairs"],
                         forced_moves=data["forced_moves"])
    db.session.add(rec)
    db.session.commit()

    # ---- run the stand-alone fixer --------------------------------------
    proc = sh([sys.executable, SPEC_SCRIPT],
              cwd=os.path.dirname(SPEC_SCRIPT),
              capture_output=True, text=True)

    if proc.returncode == 0:
        return jsonify(ok=True)           
    else:
        # log stderr for debugging
        print("[apply_specifications]", proc.stderr)
        return jsonify(error="spec-apply failed",
                       details=proc.stderr[:500]), 500

# run_allocation unchanged
@main.route("/run_allocation", methods=["GET", "POST"])      # <<< CHANGED
def run_allocation():
    """
    Single trigger endpoint used by *both* pages.
    It simply shells out to finalallocation.py (which reads the
    latest rows from BOTH tables) and then redirects the user.
    """
    try:
        script_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "ml_models", "finalallocation.py")
        )

        # Run the GA (both POST & GET hit here)
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(script_path),
            check=False           # we'll inspect return-code
        )

        if result.returncode == 0:
            flash("Allocation completed successfully", "success")
            return redirect(url_for("main.classes"))
        else:
            flash(f"Allocation failed:\n{result.stderr}", "error")
            return redirect(url_for("main.customisation_home"))

    except Exception as e:
        flash(f"Error running allocation: {e}", "error")
        return redirect(url_for("main.customisation_home"))

# ────────  JSON APIs  ─────────
@main.route("/api/students")
def api_students():
    return jsonify(fetch_all_students())

@main.route("/api/students/<int:sid>")
def api_student_detail(sid):
    return jsonify(fetch_student_details(sid))

@main.route("/api/classes")
def api_classes():
    return jsonify(fetch_unique_classes())

@main.route("/api/classes/<class_id>/avg_gpa")
def api_class_avg_gpa(class_id):
    sql = text("""
        SELECT AVG(p.perc_academic) AS avg_gpa
          FROM participants p
          JOIN allocations a ON p.student_id = a.student_id
         WHERE a.class_id = :cid
    """)
    try:
        row = db.session.execute(sql, {"cid": class_id}).fetchone()
        avg = float(row.avg_gpa) if row.avg_gpa is not None else 0.0
        return jsonify({"class_id": class_id, "avg_gpa": avg})
    except Exception as e:
        return jsonify({"error": str(e), "class_id": class_id}), 500

# ╭────────  assistant endpoints (merged) ─────────╮
@main.route("/api/assistant/analyze", methods=["POST"])
def analyze_request():
    data = request.get_json()
    user_input = data.get("input", "")
    session_id = data.get("session_id") or session.get("session_id")

    if not user_input:
        return jsonify({"success": False, "message": "No input provided"}), 400

    # Process the user's request
    result = assistant.analyze_request(user_input, session_id=session_id)
    
    # Check if the response was successful
    if not result.get("success", False):
        return jsonify({
            "success": False, 
            "message": result.get("message", "Sorry, I couldn't process your request. Please try again.")
        })
    
    # Return the successful response
    return jsonify({
        "success": True,
        "message": result.get("message", ""),
        "intent": result.get("intent", "unknown"),
        "is_modified": result.get("is_modified", False),
        "modified_params": result.get("modified_params", [])
    })

@main.route("/api/assistant/reset", methods=["GET"])
def reset_chat():
    session_id = request.args.get("session_id") or session.get("session_id")
    if not session_id:
        return jsonify({"success": False, "message": "No session ID provided"}), 400
        
    assistant.clear_chat_history(session_id=session_id)
    return jsonify({"success": True, "message": "Chat history reset successfully"})

@main.route("/api/assistant/recommendations")
def get_recommendations():
    try:
        student_data = request.args.get("student_data")
        data_obj = json.loads(student_data) if student_data else None
        recs = assistant.get_recommendations(student_data=data_obj)
        return jsonify({"success": True, "recommendations": recs})
    except Exception as e:
        default = [
            "Explore the social-network graph for relationship insights.",
            "Balance influential students across classes.",
            "Review class allocation settings regularly.",
        ]
        return jsonify({"success": True, "recommendations": default})

# ──────────────────────────────────────────