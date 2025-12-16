import streamlit as st
import requests
import pandas as pd
import plotly.express as px
from datetime import datetime

# --- CONFIGURATION ---
CURRENT_YEAR = datetime.now().year
# To estimate the "upcoming" IF, we look at citations in the current year 
# to papers published in the prior two years.
TARGET_YEARS = [CURRENT_YEAR - 1, CURRENT_YEAR - 2]

# ISSNs for the journals you requested
JOURNALS = {
    "Lancet Gastro & Hep": "2468-1253",
    "Nat Rev Gastro & Hep": "1759-5053",
    "J. Hepatology": "0168-8278",
    "Gastroenterology": "0016-5085",
    "Gut": "0017-5749",
    "Hepatology": "0270-9139"
}

# --- FUNCTIONS ---

@st.cache_data(ttl=3600*24) # Cache data for 24 hours to prevent spamming the API
def fetch_journal_data(journal_name, issn):
    """
    Fetches articles/reviews from the previous 2 years for a specific journal.
    """
    base_url = "https://api.openalex.org/works"
    works = []
    
    # We need to paginate because journals publish many papers
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
            # We only need specific fields to keep it light
            "select": "id,title,publication_year,type,counts_by_year,primary_topic,cited_by_count"
        }
        
        try:
            r = requests.get(base_url, params=params)
            r.raise_for_status()
            data = r.json()
            
            results = data.get('results', [])
            works.extend(results)
            
            cursor = data['meta'].get('next_cursor')
            if not results: # Stop if no more results
                break
                
        except Exception as e:
            st.error(f"Error fetching data for {journal_name}: {e}")
            break

    # Process the raw data
    processed_data = []
    current_year_citations = 0
    
    for work in works:
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
            "Title": work['title'],
            "Year": work['publication_year'],
            "Type": work['type'],
            "Topic": topic,
            "Citations_Current_Year": citations_this_year,
            "Total_Citations": work['cited_by_count']
        })
        
    return pd.DataFrame(processed_data)

# --- DASHBOARD LAYOUT ---

st.set_page_config(page_title="Live IF Tracker", layout="wide")
st.title(f"📊 Live Impact Factor Tracker ({CURRENT_YEAR})")
st.markdown(f"Tracking citations in **{CURRENT_YEAR}** to articles published in **{TARGET_YEARS[0]} & {TARGET_YEARS[1]}**.")
st.warning("Note: OpenAlex data updates approx. every 2 weeks. This is an *estimation* metric.")

if st.button("Refresh Data (This may take a minute)"):
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

full_df = pd.concat(all_data, ignore_index=True)

# 2. CALCULATE METRICS
metrics = []
for journal in JOURNALS.keys():
    j_df = full_df[full_df["Journal"] == journal]
    numerator = j_df["Citations_Current_Year"].sum()
    denominator = len(j_df) # Count of citable items
    
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

# 3. DISPLAY TOP LEVEL STATS
col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("🏆 Live Impact Factors")
    st.dataframe(metrics_df.style.highlight_max(axis=0, subset=["Est. Impact Factor"]), hide_index=True)

with col2:
    fig_bar = px.bar(metrics_df, x="Journal", y="Est. Impact Factor", 
                     color="Journal", title="Real-Time IF Comparison", text_auto=True)
    st.plotly_chart(fig_bar, use_container_width=True)

st.divider()

# 4. DRILL DOWN: ARTICLE TYPES
st.subheader("📂 Contribution by Article Type")
col3, col4 = st.columns(2)

with col3:
    # Aggregating by Journal and Type
    type_df = full_df.groupby(["Journal", "Type"])["Citations_Current_Year"].sum().reset_index()
    fig_type = px.bar(type_df, x="Journal", y="Citations_Current_Year", color="Type", 
                      title="Citation Volume by Type (Articles vs Reviews)", barmode="stack")
    st.plotly_chart(fig_type, use_container_width=True)

with col4:
    # Impact efficiency by type
    avg_cit_type = full_df.groupby(["Journal", "Type"])["Citations_Current_Year"].mean().reset_index()
    fig_eff = px.bar(avg_cit_type, x="Journal", y="Citations_Current_Year", color="Type",
                     title="Avg Citations Per Paper by Type (Quality Metric)", barmode="group")
    fig_eff.update_layout(yaxis_title="Avg Citations per Paper")
    st.plotly_chart(fig_eff, use_container_width=True)

st.divider()

# 5. DRILL DOWN: TOPICS
st.subheader("🧠 Top Topics Driving Impact")
selected_journal = st.selectbox("Select Journal to Analyze Topics:", list(JOURNALS.keys()))

topic_df = full_df[full_df["Journal"] == selected_journal]
topic_stats = topic_df.groupby("Topic").agg(
    Papers=('Title', 'count'),
    Total_Citations=('Citations_Current_Year', 'sum')
).reset_index()

# Filter for meaningful topics (at least 2 papers)
topic_stats = topic_stats[topic_stats["Papers"] > 1]
topic_stats["Citations_Per_Paper"] = (topic_stats["Total_Citations"] / topic_stats["Papers"]).round(1)

# Sort by total citations
top_topics = topic_stats.sort_values("Total_Citations", ascending=False).head(10)

fig_topic = px.scatter(top_topics, x="Papers", y="Citations_Per_Paper", size="Total_Citations", 
                       hover_name="Topic", color="Topic",
                       title=f"Top 10 Topics for {selected_journal} (Size = Total Citations)")
st.plotly_chart(fig_topic, use_container_width=True)

st.write("Raw Topic Data:")
st.dataframe(top_topics.sort_values("Citations_Per_Paper", ascending=False), hide_index=True)
