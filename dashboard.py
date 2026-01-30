import streamlit as st
import pandas as pd
import requests
import datetime
import time
import plotly.express as px

# -----------------------------------------------------------------------------
# CONFIGURATION & CONSTANTS
# -----------------------------------------------------------------------------

JOURNALS = {
    "The Lancet Gastroenterology & Hepatology": "2468-1156",
    "Gastroenterology": "0016-5085",
    "Gut": "0017-5749",
    "Hepatology (AASLD)": "0270-9139", # Validated AASLD ISSN
    "Journal of Hepatology (EASL)": "0168-8278",
    "Nature Reviews Gastroenterology & Hepatology": "1759-5045",
    "The Lancet": "0140-6736",
    "New England Journal of Medicine": "0028-4793",
    "Nature Medicine": "1078-8956",
    "The BMJ": "0959-8138"
}

# Streamlit Page Config
st.set_page_config(page_title="Live Impact Factor Tracker", layout="wide", page_icon="ca")

# -----------------------------------------------------------------------------
# HELPER FUNCTIONS
# -----------------------------------------------------------------------------

def get_scopus_denominator(issn, year_1, year_2, api_key):
    """
    Queries Scopus Search API to get the count of Articles (ar) and Reviews (re)
    for the specific ISSN and publication years.
    """
    url = "https://api.elsevier.com/content/search/scopus"
    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json"
    }
    
    # Impact Factor Denominator = Citable Items (Articles + Reviews) in Y-1 and Y-2
    query = f"ISSN({issn}) AND PUBYEAR > {year_1 - 1} AND PUBYEAR < {year_2 + 1} AND (DOCTYPE(ar) OR DOCTYPE(re))"
    
    params = {
        "query": query,
        "count": 1  # We only need the total-results metadata, not the entries
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        total_results = data.get('search-results', {}).get('opensearch:totalResults', 0)
        return int(total_results)
    except requests.exceptions.RequestException as e:
        st.error(f"Scopus API Error for ISSN {issn}: {e}")
        return 0
    except Exception as e:
        st.error(f"Error parsing Scopus data for ISSN {issn}: {e}")
        return 0

def get_openalex_numerator(issn, year_1, year_2, target_year):
    """
    Fetches all works from OpenAlex for the journal in year_1 and year_2.
    Sums the citations received specifically in `target_year`.
    """
    base_url = "https://api.openalex.org/works"
    citations_in_target_year = 0
    
    # Filter: Venue ISSN AND Publication Year in (Y-1, Y-2)
    # OpenAlex filter syntax: publication_year:2023|2024
    filter_param = f"primary_location.source.issn:{issn},publication_year:{year_1}|{year_2}"
    
    # Cursor pagination to fetch all works
    cursor = "*"
    per_page = 200
    
    while True:
        params = {
            "filter": filter_param,
            "per_page": per_page,
            "cursor": cursor,
            "select": "id,counts_by_year" # Optimization: Only fetch citation breakdown
        }
        
        try:
            r = requests.get(base_url, params=params, timeout=10)
            if r.status_code == 429:
                time.sleep(1) # Polite backoff
                continue
            r.raise_for_status()
            data = r.json()
            
            results = data.get('results', [])
            if not results:
                break
                
            for work in results:
                # counts_by_year is a list of dicts: [{'year': 2024, 'cited_by_count': 10}, ...]
                cby = work.get('counts_by_year', [])
                for entry in cby:
                    if entry.get('year') == target_year:
                        citations_in_target_year += entry.get('cited_by_count', 0)
            
            cursor = data['meta']['next_cursor']
            if not cursor:
                break
                
        except Exception as e:
            # st.warning(f"Partial failure on OpenAlex fetch for {issn}: {e}")
            break
            
    return citations_in_target_year

# -----------------------------------------------------------------------------
# MAIN APP UI
# -----------------------------------------------------------------------------

st.title("Live Impact Factor Dashboard")
st.markdown("""
<style>
    .metric-card {
        background-color: #262730;
        padding: 20px;
        border-radius: 10px;
        border: 1px solid #41444b;
        text-align: center;
    }
    .big-font {
        font-size: 24px !important;
        font-weight: bold;
        color: #ffffff;
    }
    .sub-font {
        font-size: 14px !important;
        color: #a0a0a0;
    }
</style>
""", unsafe_allow_html=True)

st.warning("⚠️ **Architecture Note:** This tool queries OpenAlex for citations (Numerator) and Scopus for Article/Review counts (Denominator). Using Scopus for the denominator is strictly more accurate than OpenAlex type mapping.")

with st.sidebar:
    st.header("Configuration")
    
    # API Key Handling
    # Note: In a real hosted env, use st.secrets. For this prototype, input is required.
    scopus_key = st.text_input("Scopus API Key", value="fabdac4625e5ed5417f94a0a012eec14", type="password")
    
    current_date = datetime.date.today()
    
    # Logic for "Live" IF
    # If today is Jan 2026, the "Live" IF usually refers to the 2025 IF (released June 2026).
    # IF 2025 = Cites in 2025 to pubs in 2023+2024.
    default_year = current_date.year - 1 # Default to last full year
    
    target_year = st.number_input("Impact Factor Year (Numerator Year)", 
                                  min_value=2020, 
                                  max_value=current_date.year, 
                                  value=default_year,
                                  help="The year in which citations are counted. For the 2025 IF, select 2025.")
    
    pub_year_1 = target_year - 1
    pub_year_2 = target_year - 2
    
    st.info(f"""
    **Calculation Logic:**
    * **Citations (Num):** Count of citations in **{target_year}**...
    * **Publications (Denom):** ...to items published in **{pub_year_1}** and **{pub_year_2}**.
    """)
    
    run_btn = st.button("Calculate Live IF", type="primary")

if run_btn and scopus_key:
    results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total_journals = len(JOURNALS)
    
    for idx, (name, issn) in enumerate(JOURNALS.items()):
        status_text.text(f"Processing {name}...")
        
        # 1. Get Denominator (Scopus)
        denominator = get_scopus_denominator(issn, pub_year_1, pub_year_2, scopus_key)
        
        # 2. Get Numerator (OpenAlex)
        if denominator > 0:
            numerator = get_openalex_numerator(issn, pub_year_1, pub_year_2, target_year)
            if_calc = numerator / denominator
        else:
            numerator = 0
            if_calc = 0.0
            
        results.append({
            "Journal": name,
            "ISSN": issn,
            "Citations (Num)": numerator,
            "Citable Items (Denom)": denominator,
            "Live IF": round(if_calc, 3)
        })
        
        progress_bar.progress((idx + 1) / total_journals)
    
    status_text.text("Calculation Complete.")
    progress_bar.empty()
    
    # Display Results
    df = pd.DataFrame(results)
    df = df.sort_values("Live IF", ascending=False).reset_index(drop=True)
    
    # 1. Top Level Metrics
    col1, col2, col3 = st.columns(3)
    top_journal = df.iloc[0]
    with col1:
        st.metric("Highest IF", f"{top_journal['Journal']}", f"{top_journal['Live IF']}")
    
    # 2. Chart
    st.subheader(f"Projected {target_year} Impact Factor")
    fig = px.bar(df, x="Live IF", y="Journal", orientation='h', 
                 text="Live IF", color="Live IF", 
                 color_continuous_scale="Viridis")
    fig.update_layout(showlegend=False, height=500)
    st.plotly_chart(fig, use_container_width=True)
    
    # 3. Detailed Data Table
    st.subheader("Detailed Data")
    st.dataframe(
        df.style.format({
            "Live IF": "{:.3f}",
            "Citable Items (Denom)": "{:,.0f}",
            "Citations (Num)": "{:,.0f}"
        }),
        use_container_width=True
    )
    
    # CSV Download
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        "Download Data as CSV",
        csv,
        "live_impact_factors.csv",
        "text/csv",
        key='download-csv'
    )

elif run_btn and not scopus_key:
    st.error("Please enter a Scopus API Key.")
else:
    st.info("Click 'Calculate Live IF' to start fetching data. This process may take a minute due to API rate limits.")
