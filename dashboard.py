import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import re
from datetime import datetime

# --- CONFIGURATION ---
CURRENT_YEAR = datetime.now().year
# Tracking citations in current year to papers from previous 2 years
TARGET_YEARS = [CURRENT_YEAR - 1, CURRENT_YEAR - 2]

# ISSNs for the journals
JOURNALS = {
    "Lancet Gastro & Hep": "2468-1253",
    "Nat Rev Gastro & Hep": "1759-5053",
    "J. Hepatology": "0168-8278",
    "Gastroenterology": "0016-5085",
    "Gut": "0017-5749",
    "Hepatology": "0270-9139"
}

# --- FILTERS ---

# 1. Keyword Blocklist (General)
ABSTRACT_KEYWORDS = [
    "abstracts of", "abstracts from", "meeting abstracts", 
    "congress", "supplement", "poster presentation", 
    "oral presentation", "proceedings of", "late-breaking"
]

# 2. Regex Patterns for Specific Journals (J Hep & Gastro)
# Catches "SAT-123", "WED-306", "OS-096-YI", "THU-106-YI"
REGEX_JHEP = r"^(?:SAT|SUN|MON|TUE|WED|THU|FRI|OS|PS|LBP)-\d+(?:-[A-Z0-9]+)?\b"

# Catches "Tu1542", "Mo1249", "Su1000" (DDW Format)
REGEX_GASTRO = r"^(?:Mo|Tu|We|Th|Fr|Sa|Su)\d{3,4}\b"

# Combine into one pattern for efficiency
REGEX_COMBINED = f"({REGEX_JHEP})|({REGEX_GASTRO})"

# --- FUNCTIONS ---

def is_meeting_abstract(title):
    """
    Returns True if the title matches known meeting abstract patterns.
    """
    if not title:
        return False
    
    # Check 1: General Keywords
    title_lower = title.lower()
    for kw in ABSTRACT_KEYWORDS:
        if kw in title_lower:
            return True
            
    # Check 2: Specific Regex Patterns (J Hep / Gastro)
    if re.search(REGEX_COMBINED, title):
        return True
        
    return False

@st.cache_data(ttl=3600*24)
def fetch_journal_data(journal_name, issn):
    base_url = "https://api.openalex.org/works"
    works = []
    
    cursor = "*"
    while cursor:
        params = {
            "filter": (
                f"primary_location.source.issn:{issn},"
                f"publication_year:{'|'.join(map(str, TARGET_YEARS))},"
                f"type:article|review"
            ),
            "per_page": 200,
            "cursor": cursor,
            "select": "id,title,publication_year,type,counts_by_year,primary_topic,cited_by_count"
        }
        
        try:
            r = requests.get(base_url, params=params)
            r.raise_for_status()
            data = r.json()
            
            results = data.get('results', [])
            works.extend(results)
            
            cursor = data['meta'].get('next_cursor')
            if not results:
                break
                
        except Exception as e:
            st.error(f"Error fetching data for {journal_name}: {e}")
            break

    # Processing
    processed_data = []
    
    for work in works:
        title = work.get('title')
        if not title:
            title = "Untitled"
        
        # --- EXCLUSION LOGIC ---
        if is_meeting_abstract(title):
            continue
        # -----------------------

        # Calculate citations received ONLY in the current year
        citations_this_year = 0
        if work.get('counts_by_year'):
            for count in work['counts_by_year']:
                if count['year'] == CURRENT_YEAR:
                    citations_this_year = count['cited_by_count']
                    break
        
        # Get Topic
        topic = "Unknown"
        if work.get('primary_topic'):
            topic = work['primary_topic'].get('display_name', 'Unknown')

        processed_data.append({
            "Journal": journal_name,
            "Title": title,
            "Year": work['publication_year'],
            "Type": work['type'],
            "Topic": topic,
            "Citations_Current_Year": citations_this_year,
            "Total_Citations": work['cited_by_count']
        })
        
    return pd.DataFrame(processed_data)

# --- DASHBOARD LAYOUT ---

st.set_page_config(page_title="Live IF Tracker", layout="wide")
st.title(f"Live Impact Factor Tracker ({CURRENT_YEAR})")
st.markdown(f"Tracking citations in **{CURRENT_YEAR}** to articles published in **{TARGET_YEARS[0]} & {TARGET_YEARS[1]}**.")

if st.button("Refresh Data"):
    st.cache_data.clear()

# 1. FETCH DATA
all_data = []
progress_bar = st.progress(0)
status_text = st.empty()

for i, (name, issn) in enumerate(JOURNALS.items()):
    status_text.text(f"Fetching data for {name}...")
    df = fetch_journal_data(name, issn)
    all_data.append(df)
    progress_bar.progress((i + 1) / len(JOURNALS))

status_text.empty()
progress_bar.empty()

if not all_data:
    st.error("No data fetched. Please check API status.")
    st.stop()

full_df = pd.concat(all_data, ignore_index=True)

# 2. CALCULATE METRICS
metrics = []
for journal in JOURNALS.keys():
    j_df = full_df[full_df["Journal"] == journal]
    numerator = j_df["Citations_Current_Year"].sum()
    denominator = len(j_df) 
    
    if denominator > 0:
        est_if = numerator / denominator
    else:
        est_if = 0
        
    metrics.append({
        "Journal": journal,
        "Est. Impact Factor": round(est_if, 2),
        "Citations (Num)": numerator,
        "Articles (Den)": denominator
    })

metrics_df = pd.DataFrame(metrics).sort_values("Est. Impact Factor", ascending=False)

# 3. TOP LEVEL STATS
col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("Live Impact Factors")
    st.dataframe(metrics_df.style.highlight_max(axis=0, subset=["Est. Impact Factor"]), hide_index=True)

with col2:
    fig_bar = px.bar(metrics_df, x="Journal", y="Est. Impact Factor", 
                     color="Journal", title="Real-Time IF Comparison", text_auto=True)
    fig_bar.update_layout(showlegend=False)
    st.plotly_chart(fig_bar, use_container_width=True)

st.divider()

# 4. CITATION DISTRIBUTION (HISTOGRAM)
st.subheader("Citation Distribution (The Long Tail)")
st.markdown("This view helps identify if the IF is driven by a few outliers or consistent performance.")

selected_journals_hist = st.multiselect(
    "Select Journals to Compare Distributions:", 
    options=list(JOURNALS.keys()),
    default=["Lancet Gastro & Hep", "Gastroenterology"]
)

if selected_journals_hist:
    hist_df = full_df[full_df["Journal"].isin(selected_journals_hist)]
    
    fig_hist = px.histogram(
        hist_df, 
        x="Citations_Current_Year", 
        color="Journal", 
        barmode="overlay",
        nbins=50,
        opacity=0.6,
        title="Distribution of Papers by Citation Count (Current Year)",
        labels={"Citations_Current_Year": "Citations Received in Current Year"}
    )
    fig_hist.update_layout(xaxis_title="Citations per Paper", yaxis_title="Number of Papers")
    st.plotly_chart(fig_hist, use_container_width=True)

st.divider()

# 5. PAPER LIST EXPLORER
st.subheader("Paper Explorer")
st.markdown("Filter and sort to identify top papers or inspect excluded items.")

col_filter1, col_filter2 = st.columns(2)
with col_filter1:
    filter_journal = st.multiselect("Filter by Journal", options=list(JOURNALS.keys()), default=list(JOURNALS.keys()))
with col_filter2:
    filter_type = st.multiselect("Filter by Type", options=full_df["Type"].unique(), default=full_df["Type"].unique())

table_df = full_df[
    (full_df["Journal"].isin(filter_journal)) & 
    (full_df["Type"].isin(filter_type))
]

st.dataframe(
    table_df.sort_values("Citations_Current_Year", ascending=False),
    column_config={
        "Citations_Current_Year": st.column_config.NumberColumn("Citations (Current Year)", format="%d"),
        "Total_Citations": st.column_config.NumberColumn("Total Citations", format="%d"),
        "Year": st.column_config.NumberColumn("Year", format="%d")
    },
    use_container_width=True,
    hide_index=True
)
