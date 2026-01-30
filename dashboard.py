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
    "Hepatology (AASLD)": "0270-9139",
    "Journal of Hepatology (EASL)": "0168-8278",
    "Nature Reviews Gastroenterology & Hepatology": "1759-5045",
    "The Lancet": "0140-6736",
    "New England Journal of Medicine": "0028-4793",
    "Nature Medicine": "1078-8956",
    "The BMJ": "0959-8138"
}

st.set_page_config(page_title="Live Impact Factor Tracker", layout="wide", page_icon="ca")

# -----------------------------------------------------------------------------
# HELPER FUNCTIONS
# -----------------------------------------------------------------------------

def get_scopus_denominator(issn, year_1, year_2, api_key, debug=False):
    """
    Queries Scopus Search API for citable items (Articles + Reviews).
    """
    url = "https://api.elsevier.com/content/search/scopus"
    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json"
    }
    
    # Query: ISSN(...) AND PUBYEAR range AND (Article OR Review)
    query = f"issn({issn}) AND PUBYEAR > {year_1 - 1} AND PUBYEAR < {year_2 + 1} AND (DOCTYPE(ar) OR DOCTYPE(re))"
    
    params = {
        "query": query,
        "count": 0,  # We only need metadata (totalResults)
        "httpAccept": "application/json"
    }
    
    try:
        if debug:
            st.write(f"**[Scopus Request]** ISSN: {issn} | Query: `{query}`")
            
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        if debug:
            st.write(f"**[Scopus Response]** Status: {response.status_code}")
            if response.status_code != 200:
                st.write(f"Response Text: {response.text}")

        response.raise_for_status()
        data = response.json()
        
        # Scopus JSON structure varies slightly by error, but success looks like this:
        total_results = data.get('search-results', {}).get('opensearch:totalResults', 0)
        return int(total_results)

    except requests.exceptions.HTTPError as e:
        if debug:
            st.error(f"Scopus HTTP Error: {e}")
        return 0
    except Exception as e:
        if debug:
            st.error(f"Scopus General Error: {e}")
        return 0

def get_openalex_numerator(issn, year_1, year_2, target_year, debug=False):
    """
    Fetches works from OpenAlex (Year 1 & 2) and sums citations from Target Year.
    """
    base_url = "https://api.openalex.org/works"
    citations_in_target_year = 0
    
    # Using 'locations.source.issn' is broader/safer than 'primary_location.source.issn'
    filter_param = f"locations.source.issn:{issn},publication_year:{year_1}|{year_2}"
    
    per_page = 200
    cursor = "*"
    
    # Safety break to prevent infinite loops during testing
    max_pages = 50 
    page_count = 0
    
    if debug:
        st.write(f"**[OpenAlex Request]** Filter: `{filter_param}`")

    while True:
        params = {
            "filter": filter_param,
            "per_page": per_page,
            "cursor": cursor,
            "select": "id,counts_by_year"
        }
        
        try:
            r = requests.get(base_url, params=params, timeout=10)
            if r.status_code == 429:
                time.sleep(1)
                continue
            r.raise_for_status()
            data = r.json()
            
            results = data.get('results', [])
            if not results:
                break
                
            for work in results:
                cby = work.get('counts_by_year', [])
                for entry in cby:
                    if entry.get('year') == target_year:
                        citations_in_target_year += entry.get('cited_by_count', 0)
            
            cursor = data['meta']['next_cursor']
            page_count += 1
            
            if not cursor or page_count > max_pages:
                break
                
        except Exception as e:
            if debug:
                st.error(f"OpenAlex Error for {issn}: {e}")
            break
            
    return citations_in_target_year

# -----------------------------------------------------------------------------
# UI LAYOUT
# -----------------------------------------------------------------------------

st.title("Live Impact Factor Dashboard")

with st.sidebar:
    st.header("Configuration")
    scopus_key = st.text_input("Scopus API Key", value="fabdac4625e5ed5417f94a0a012eec14", type="password")
    
    current_date = datetime.date.today()
    default_year = current_date.year - 1
    
    target_year = st.number_input("Target Citation Year", min_value=2020, max_value=current_date.year, value=default_year)
    pub_year_1 = target_year - 1
    pub_year_2 = target_year - 2
    
    st.info(f"Measuring citations in **{target_year}** to papers published in **{pub_year_1}** & **{pub_year_2}**.")
    
    debug_mode = st.checkbox("Show Debug Logs", value=False, help="Check this if you are getting 0 results to see API errors.")
    run_btn = st.button("Calculate Live IF", type="primary")

if run_btn:
    if not scopus_key:
        st.error("Scopus API Key is required.")
        st.stop()

    results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total_journals = len(JOURNALS)
    
    for idx, (name, issn) in enumerate(JOURNALS.items()):
        status_text.markdown(f"**Processing:** {name} (`{issn}`)")
        
        # 1. OpenAlex (Numerator) - We run this regardless of Scopus success
        numerator = get_openalex_numerator(issn, pub_year_1, pub_year_2, target_year, debug=debug_mode)
        
        # 2. Scopus (Denominator)
        denominator = get_scopus_denominator(issn, pub_year_1, pub_year_2, scopus_key, debug=debug_mode)
        
        if denominator > 0:
            if_calc = numerator / denominator
        else:
            if_calc = 0.0
            
        results.append({
            "Journal": name,
            "ISSN": issn,
            "Citations (Num)": numerator,
            "Citable Items (Denom)": denominator,
            "Live IF": round(if_calc, 3)
        })
        
        progress_bar.progress((idx + 1) / total_journals)
    
    status_text.success("Calculation Complete")
    progress_bar.empty()
    
    # Results Display
    df = pd.DataFrame(results)
    
    # Warning if zeros detected
    if df['Citable Items (Denom)'].sum() == 0:
        st.error("⚠️ Scopus returned 0 items for all journals. Check your API Key permissions or enable Debug Mode.")
    
    df_sorted = df.sort_values("Live IF", ascending=False).reset_index(drop=True)
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("Impact Factor Rankings")
        fig = px.bar(df_sorted, x="Live IF", y="Journal", orientation='h', 
                     text="Live IF", color="Live IF", title=f"Projected IF {target_year}")
        fig.update_layout(yaxis={'categoryorder':'total ascending'})
        st.plotly_chart(fig, use_container_width=True)
        
    with col2:
        st.subheader("Data Table")
        st.dataframe(
            df_sorted[["Journal", "Live IF", "Citations (Num)", "Citable Items (Denom)"]],
            use_container_width=True,
            hide_index=True
        )
