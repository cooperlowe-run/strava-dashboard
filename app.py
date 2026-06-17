import streamlit as st
import requests
import json
import os
import csv
import io
import plotly.graph_objects as go
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

SPORT_COLORS = {
    "XC":    "#2e7d32",
    "Track": "#1565c0",
    "Road":  "#6a1b9a",
    "Other": "#555555",
}

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
            month = act["start_date"][:7]
            monthly[month] += act["distance"] / 1609.34
    return dict(sorted(monthly.items()))

def aggregate_weekly(activities):
    from datetime import datetime, timedelta
    weekly = defaultdict(float)
    for act in activities:
        if act.get("type") in ("Run", "VirtualRun"):
            d = datetime.strptime(act["start_date"][:10], "%Y-%m-%d")
            week_start = d - timedelta(days=d.weekday())  # Monday of that week
            weekly[week_start.strftime("%Y-%m-%d")] += act["distance"] / 1609.34
    return dict(sorted(weekly.items()))

def pace_seconds_to_str(total_seconds, distance_miles):
    if distance_miles <= 0:
        return "—"
    pace = total_seconds / distance_miles
    mins, secs = divmod(int(pace), 60)
    return f"{mins}:{secs:02d}/mi"

def time_str_to_seconds(t):
    parts = t.strip().split(":")
    parts = [int(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return int(parts[0])

def parse_csv_races(file_bytes):
    text   = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    imported, errors = [], []
    for i, row in enumerate(reader, start=2):
        row = {k.strip().lower(): v.strip() for k, v in row.items()}
        try:
            name     = row.get("name", "").strip()
            date     = row.get("date", "").strip()
            dist_raw = row.get("distance", "0").strip()
            time_raw = row.get("time", "").strip()
            sport    = row.get("sport", "Road").strip().title()
            if not name or not date or not time_raw:
                continue
            dist_str = dist_raw.lower().replace("mi","").replace("km","").replace("m","").strip()
            distance = float(dist_str) if dist_str else 0.0
            secs = time_str_to_seconds(time_raw)
            if sport not in SPORT_COLORS:
                sport = "Other"
            imported.append({
                "name": name, "date": date, "distance": distance,
                "seconds": secs, "time_str": time_raw, "sport": sport,
            })
        except Exception as e:
            errors.append(f"Row {i}: {e}")
    return imported, errors

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

# ── Sidebar: filters ──────────────────────────────────────────────────────────
st.sidebar.header("Filters")
view_mode = st.sidebar.radio("View mileage by", ["Monthly", "Weekly"], horizontal=True)

if view_mode == "Weekly":
    aggregated = aggregate_weekly(activities)
    bar_label  = "Weekly miles"
else:
    aggregated = aggregate_monthly(activities)
    bar_label  = "Monthly miles"

all_years = sorted({m[:4] for m in aggregated.keys()})
selected_years  = st.sidebar.multiselect("Year(s)", all_years, default=all_years)
sport_options   = list(SPORT_COLORS.keys())
selected_sports = st.sidebar.multiselect("Sport", sport_options, default=sport_options)

filtered = {m: v for m, v in aggregated.items() if m[:4] in selected_years}
months   = list(filtered.keys())
mileage  = list(filtered.values())

# ── Sidebar: log a race ───────────────────────────────────────────────────────
st.sidebar.header("Log a race")
with st.sidebar.form("race_form"):
    race_name  = st.text_input("Race name", placeholder="State 5K")
    race_date  = st.date_input("Date")
    race_dist  = st.number_input("Distance (miles)", min_value=0.1, value=3.1, step=0.1)
    race_time  = st.text_input("Finish time", placeholder="18:45  or  1:02:30")
    race_sport = st.selectbox("Sport", sport_options)
    submitted  = st.form_submit_button("Add race")

races = load_races()
if submitted and race_name and race_time:
    try:
        secs = time_str_to_seconds(race_time)
        races.append({
            "name": race_name, "date": str(race_date),
            "distance": race_dist, "seconds": secs,
            "time_str": race_time, "sport": race_sport,
        })
        save_races(races)
        st.sidebar.success(f"Added {race_name}!")
    except Exception:
        st.sidebar.error("Couldn't parse that time. Use H:MM:SS or M:SS.")

# ── Sidebar: CSV import ───────────────────────────────────────────────────────
st.sidebar.header("Import races from CSV")
template_csv = "name,date,distance,time,sport\nState Cross Country Meet,2023-11-04,3.1,18:45,XC\nMile Run,2024-04-20,1.0,4:32,Track\n"
st.sidebar.download_button("⬇️ Download CSV template", data=template_csv,
                           file_name="races_template.csv", mime="text/csv")
uploaded = st.sidebar.file_uploader("Upload races CSV", type="csv")
if uploaded:
    imported, errors = parse_csv_races(uploaded.read())
    if imported:
        existing_keys = {(r["name"], r["date"]) for r in races}
        new_races = [r for r in imported if (r["name"], r["date"]) not in existing_keys]
        races.extend(new_races)
        save_races(races)
        st.sidebar.success(f"Imported {len(new_races)} race(s) ({len(imported)-len(new_races)} duplicates skipped).")
    if errors:
        st.sidebar.warning(f"{len(errors)} row(s) skipped — check format.")

# ── Color by distance, shape by sport ────────────────────────────────────────
# Round distance to nearest standard event for bucketing
def distance_bucket(d):
    if d <= 0.26:   return "0.25 mi (400m)"
    elif d <= 0.6:  return "0.5 mi (800m)"
    elif d <= 1.1:  return "1 mi (1600m)"
    elif d <= 2.1:  return "2 mi (3200m)"
    elif d <= 3.2:  return "5K / XC"
    elif d <= 6.3:  return "10K"
    elif d <= 13.2: return "Half Marathon"
    else:           return "Marathon+"

DISTANCE_COLORS = {
    "0.25 mi (400m)":  "#e53935",   # red
    "0.5 mi (800m)":   "#fb8c00",   # orange
    "1 mi (1600m)":    "#fdd835",   # yellow
    "2 mi (3200m)":    "#43a047",   # green
    "5K / XC":         "#00acc1",   # teal
    "10K":             "#1e88e5",   # blue
    "Half Marathon":   "#8e24aa",   # purple
    "Marathon+":       "#6d4c41",   # brown
}

SPORT_SHAPES = {
    "Track": "circle",
    "XC":    "diamond",
    "Road":  "square",
    "Other": "cross",
}

# ── Main chart ────────────────────────────────────────────────────────────────
fig = go.Figure()

# Mileage bars on primary y-axis
fig.add_trace(go.Bar(
    x=months, y=mileage, name=bar_label,
    marker_color="rgba(252, 82, 0, 0.75)",
    yaxis="y1",
    hovertemplate="%{x}<br>%{y:.1f} miles<extra></extra>",
))

# Build one trace per (distance_bucket, sport) combo so legend is clean
all_paces = []
grouped = defaultdict(list)
for race in races:
    sport = race.get("sport", "Other")
    if sport not in SPORT_SHAPES:
        sport = "Other"
    if sport not in selected_sports:
        continue
    race_month = race["date"][:7]
    if not any(k.startswith(race_month) or k[:7] == race_month for k in filtered):
        continue
    if race.get("seconds", 0) > 0 and race.get("distance", 0) > 0:
        bucket = distance_bucket(race["distance"])
        grouped[(bucket, sport)].append(race)
        all_paces.append(race["seconds"] / race["distance"])

# Track which distance labels we've already added to legend (avoid duplicates)
seen_distances = set()
seen_sports = set()

for (bucket, sport), group_races in sorted(grouped.items()):
    color = DISTANCE_COLORS.get(bucket, "#888888")
    shape = SPORT_SHAPES.get(sport, "circle")
    pace_seconds = [r["seconds"] / r["distance"] for r in group_races]

    # Show distance in legend only once; show sport in legend only once
    dist_label  = bucket if bucket not in seen_distances else None
    sport_label = sport  if sport  not in seen_sports   else None
    legend_name = f"{bucket} · {sport}"
    seen_distances.add(bucket)
    seen_sports.add(sport)

    fig.add_trace(go.Scatter(
        x=[r["date"] for r in group_races],
        y=pace_seconds,
        mode="markers",
        marker=dict(
            size=13,
            color=color,
            symbol=shape,
            line=dict(width=1, color="rgba(0,0,0,0.3)"),
        ),
        name=legend_name,
        yaxis="y2",
        text=[r["name"] for r in group_races],
        customdata=[
            [r["distance"], r["time_str"],
             pace_seconds_to_str(r["seconds"], r["distance"]), sport]
            for r in group_races
        ],
        hovertemplate=(
            "<b>%{text}</b><br>"
            "Date: %{x}<br>"
            "%{customdata[0]} mi · %{customdata[1]}<br>"
            "Pace: %{customdata[2]}<br>"
            "Sport: %{customdata[3]}<extra></extra>"
        ),
    ))

# Pace axis range — slow at top, fast at bottom
pace_min = (min(all_paces) * 0.95) if all_paces else 0.0
pace_max = (max(all_paces) * 1.05) if all_paces else 600.0
step = (pace_max - pace_min) / 5
tick_vals = [pace_min + step * i for i in range(6)]   # slow → fast top to bottom
tick_text = [pace_seconds_to_str(int(v), 1) for v in tick_vals]

fig.update_layout(
    xaxis=dict(title="Date", showgrid=False),
    yaxis=dict(
        title=dict(text="Miles", font=dict(color="rgba(252, 82, 0, 0.9)")),
        tickfont=dict(color="rgba(252, 82, 0, 0.9)"),
        gridcolor="rgba(128,128,128,0.15)",
    ),
    yaxis2=dict(
        title=dict(text="Pace (min/mile)"),
        overlaying="y",
        side="right",
        showgrid=False,
        autorange="reversed",
        tickvals=tick_vals,
        ticktext=tick_text,
    ),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    hovermode="closest",
    margin=dict(t=40, b=40),
    height=500,
)

st.plotly_chart(fig, use_container_width=True)

# ── Summary stats ─────────────────────────────────────────────────────────────
visible_races = [
    r for r in races
    if r["date"][:4] in selected_years
    and r.get("sport", "Other") in selected_sports
]
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total miles",     f"{sum(mileage):,.0f}")
col2.metric("Avg miles/month", f"{sum(mileage)/len(mileage):.1f}" if mileage else "—")
col3.metric("Peak month",      f"{max(mileage):.1f} mi" if mileage else "—")
col4.metric("Races logged",    len(visible_races))

# ── Race history table with edit / delete ─────────────────────────────────────
st.subheader("Race history")

if not races:
    st.info("No races logged yet. Add one using the sidebar.")
else:
    # Init session state for which race is being edited
    if "editing_index" not in st.session_state:
        st.session_state["editing_index"] = None

    sorted_races = sorted(
        [(i, r) for i, r in enumerate(races)
         if r["date"][:4] in selected_years
         and r.get("sport", "Other") in selected_sports],
        key=lambda x: x[1]["date"], reverse=True
    )

    if not sorted_races:
        st.info("No races match the current filters.")
    else:
        for orig_idx, race in sorted_races:
            pace = pace_seconds_to_str(race["seconds"], race["distance"])
            sport = race.get("sport", "Other")
            color = SPORT_COLORS.get(sport, "#555")

            # ── Edit mode for this row ────────────────────────────────────────
            if st.session_state["editing_index"] == orig_idx:
                with st.form(key=f"edit_form_{orig_idx}"):
                    st.markdown(f"**Editing:** {race['name']}")
                    c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 2])
                    new_name  = c1.text_input("Name",     value=race["name"])
                    new_date  = c2.text_input("Date",     value=race["date"])
                    new_dist  = c3.number_input("Distance (mi)", min_value=0.1,
                                                value=float(race["distance"]), step=0.1)
                    new_time  = c4.text_input("Time",     value=race["time_str"])
                    new_sport = c5.selectbox("Sport", sport_options,
                                             index=sport_options.index(sport)
                                             if sport in sport_options else 0)
                    save_col, cancel_col = st.columns([1, 5])
                    save_btn   = save_col.form_submit_button("💾 Save")
                    cancel_btn = cancel_col.form_submit_button("Cancel")

                if save_btn:
                    try:
                        new_secs = time_str_to_seconds(new_time)
                        races[orig_idx] = {
                            "name": new_name, "date": new_date,
                            "distance": new_dist, "seconds": new_secs,
                            "time_str": new_time, "sport": new_sport,
                        }
                        save_races(races)
                        st.session_state["editing_index"] = None
                        st.rerun()
                    except Exception:
                        st.error("Couldn't parse that time. Use H:MM:SS or M:SS.")

                if cancel_btn:
                    st.session_state["editing_index"] = None
                    st.rerun()

            # ── Normal display row ────────────────────────────────────────────
            else:
                col_name, col_date, col_sport, col_dist, col_time, col_pace, col_edit, col_del = st.columns(
                    [3, 2, 1.5, 1.5, 1.5, 1.5, 1, 1]
                )
                col_name.markdown(f"**{race['name']}**")
                col_date.write(race["date"])
                col_sport.markdown(
                    f"<span style='color:{color};font-weight:500'>{sport}</span>",
                    unsafe_allow_html=True
                )
                col_dist.write(f"{race['distance']} mi")
                col_time.write(race["time_str"])
                col_pace.write(pace)

                if col_edit.button("✏️", key=f"edit_{orig_idx}", help="Edit this race"):
                    st.session_state["editing_index"] = orig_idx
                    st.rerun()

                if col_del.button("🗑️", key=f"del_{orig_idx}", help="Delete this race"):
                    st.session_state[f"confirm_del_{orig_idx}"] = True
                    st.rerun()

            # ── Delete confirmation ───────────────────────────────────────────
            if st.session_state.get(f"confirm_del_{orig_idx}"):
                st.warning(f"Delete **{race['name']}**? This cannot be undone.")
                yes_col, no_col = st.columns([1, 8])
                if yes_col.button("Yes, delete", key=f"yes_{orig_idx}"):
                    races.pop(orig_idx)
                    save_races(races)
                    st.session_state.pop(f"confirm_del_{orig_idx}", None)
                    st.rerun()
                if no_col.button("Cancel", key=f"no_{orig_idx}"):
                    st.session_state.pop(f"confirm_del_{orig_idx}", None)
                    st.rerun()

            st.divider()

# ── Refresh button ────────────────────────────────────────────────────────────
if st.button("🔄 Refresh Strava data"):
    del st.session_state["activities"]
    st.rerun()
