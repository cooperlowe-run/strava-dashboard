import streamlit as st
import requests
import json
import os
import plotly.graph_objects as go
from datetime import datetime
from collections import defaultdict

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Running Dashboard", page_icon="🏃", layout="wide")
st.title("🏃 Running Dashboard")
st.caption("Mileage over time + race results")

# ── Strava OAuth helpers ──────────────────────────────────────────────────────
CLIENT_ID     = st.secrets.get("STRAVA_CLIENT_ID", "")
CLIENT_SECRET = st.secrets.get("STRAVA_CLIENT_SECRET", "")
REDIRECT_URI  = st.secrets.get("REDIRECT_URI", "http://localhost:8501")

RACES_FILE = "races.json"

def load_races():
    if os.path.exists(RACES_FILE):
        with open(RACES_FILE) as f:
            return json.load(f)
    return []

def save_races(races):
    with open(RACES_FILE, "w") as f:
        json.dump(races, f)

def get_auth_url():
    return (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=activity:read_all"
    )

def exchange_code_for_token(code):
    resp = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
    })
    return resp.json()

def fetch_activities(access_token):
    activities, page = [], 1
    while True:
        resp = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"per_page": 200, "page": page},
        )
        batch = resp.json()
        if not batch:
            break
        activities.extend(batch)
        page += 1
    return activities

def aggregate_monthly(activities):
    monthly = defaultdict(float)
    for act in activities:
        if act.get("type") in ("Run", "VirtualRun"):
            month = act["start_date"][:7]           # "2024-03"
            monthly[month] += act["distance"] / 1609.34  # metres → miles
    return dict(sorted(monthly.items()))

def pace_seconds_to_str(total_seconds, distance_miles):
    """Convert finish time (seconds) to min/mile pace string."""
    if distance_miles <= 0:
        return "—"
    pace = total_seconds / distance_miles
    mins, secs = divmod(int(pace), 60)
    return f"{mins}:{secs:02d}/mi"

def time_str_to_seconds(t):
    """Accept H:MM:SS or M:SS and return total seconds."""
    parts = t.strip().split(":")
    parts = [int(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return int(parts[0])

# ── Auth flow ─────────────────────────────────────────────────────────────────
params = st.query_params
code   = params.get("code")

if "access_token" not in st.session_state:
    if code:
        with st.spinner("Connecting to Strava…"):
            token_data = exchange_code_for_token(code)
        if "access_token" in token_data:
            st.session_state["access_token"] = token_data["access_token"]
            st.query_params.clear()
            st.rerun()
        else:
            st.error("Auth failed. Check your Client ID / Secret in secrets.")
    else:
        st.info("Connect your Strava account to get started.")
        st.link_button("🔗 Connect Strava", get_auth_url())
        st.stop()

# ── Fetch + cache activities ──────────────────────────────────────────────────
if "activities" not in st.session_state:
    with st.spinner("Fetching your activities from Strava…"):
        st.session_state["activities"] = fetch_activities(st.session_state["access_token"])

activities = st.session_state["activities"]
monthly    = aggregate_monthly(activities)

if not monthly:
    st.warning("No running activities found on your Strava account.")
    st.stop()

months  = list(monthly.keys())
mileage = list(monthly.values())

# ── Sidebar: date range filter ────────────────────────────────────────────────
st.sidebar.header("Filters")
all_years = sorted({m[:4] for m in months})
selected_years = st.sidebar.multiselect("Year(s)", all_years, default=all_years)

filtered = {m: v for m, v in monthly.items() if m[:4] in selected_years}
months   = list(filtered.keys())
mileage  = list(filtered.values())

# ── Race log ──────────────────────────────────────────────────────────────────
st.sidebar.header("Log a race")
with st.sidebar.form("race_form"):
    race_name = st.text_input("Race name", placeholder="Boston Marathon")
    race_date = st.date_input("Date")
    race_dist = st.number_input("Distance (miles)", min_value=0.1, value=26.2, step=0.1)
    race_time = st.text_input("Finish time", placeholder="3:45:00  or  22:30")
    submitted = st.form_submit_button("Add race")

races = load_races()
if submitted and race_name and race_time:
    try:
        secs = time_str_to_seconds(race_time)
        races.append({
            "name":     race_name,
            "date":     str(race_date),
            "distance": race_dist,
            "seconds":  secs,
            "time_str": race_time,
        })
        save_races(races)
        st.sidebar.success(f"Added {race_name}!")
    except Exception:
        st.sidebar.error("Couldn't parse that time. Use H:MM:SS or M:SS.")

# ── Main chart ────────────────────────────────────────────────────────────────
fig = go.Figure()

# Mileage bars
fig.add_trace(go.Bar(
    x=months,
    y=mileage,
    name="Monthly miles",
    marker_color="rgba(252, 82, 0, 0.75)",   # Strava orange
    hovertemplate="%{x}<br>%{y:.1f} miles<extra></extra>",
))

# 3-month rolling average
if len(mileage) >= 3:
    rolling = []
    for i in range(len(mileage)):
        window = mileage[max(0, i-2):i+1]
        rolling.append(sum(window) / len(window))
    fig.add_trace(go.Scatter(
        x=months,
        y=rolling,
        name="3-mo avg",
        mode="lines",
        line=dict(color="rgba(252, 82, 0, 0.4)", width=2, dash="dot"),
        hovertemplate="%{x}<br>avg: %{y:.1f} mi<extra></extra>",
    ))

# Race markers overlaid
for race in races:
    month = race["date"][:7]
    if month not in filtered:
        continue
    pace = pace_seconds_to_str(race["seconds"], race["distance"])
    fig.add_trace(go.Scatter(
        x=[month],
        y=[filtered.get(month, 0)],
        mode="markers+text",
        marker=dict(size=14, color="#1a1a2e", symbol="star"),
        text=[race["name"]],
        textposition="top center",
        name=race["name"],
        hovertemplate=(
            f"<b>{race['name']}</b><br>"
            f"{race['distance']} mi · {race['time_str']}<br>"
            f"Pace: {pace}<extra></extra>"
        ),
    ))

fig.update_layout(
    xaxis_title="Month",
    yaxis_title="Miles",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    hovermode="x unified",
    margin=dict(t=40, b=40),
    height=480,
)
fig.update_xaxes(showgrid=False)
fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)")

st.plotly_chart(fig, use_container_width=True)

# ── Summary stats ─────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total miles", f"{sum(mileage):,.0f}")
col2.metric("Avg miles/month", f"{sum(mileage)/len(mileage):.1f}" if mileage else "—")
col3.metric("Peak month", f"{max(mileage):.1f} mi" if mileage else "—")
col4.metric("Races logged", len([r for r in races if r["date"][:4] in selected_years]))

# ── Race history table ────────────────────────────────────────────────────────
if races:
    st.subheader("Race history")
    race_rows = []
    for r in sorted(races, key=lambda x: x["date"], reverse=True):
        if r["date"][:4] not in selected_years:
            continue
        pace = pace_seconds_to_str(r["seconds"], r["distance"])
        race_rows.append({
            "Race":     r["name"],
            "Date":     r["date"],
            "Distance": f"{r['distance']} mi",
            "Time":     r["time_str"],
            "Pace":     pace,
        })
    if race_rows:
        st.dataframe(race_rows, use_container_width=True, hide_index=True)

# ── Refresh button ────────────────────────────────────────────────────────────
if st.button("🔄 Refresh Strava data"):
    del st.session_state["activities"]
    st.rerun()

