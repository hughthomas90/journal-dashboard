import streamlit as st
import pandas as pd
import requests
import datetime
import time
import math
import plotly.express as px

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------

# Dictionary: Name -> List of ISSNs (Print and Electronic to ensure coverage)
JOURNALS = {
    "The Lancet Gastroenterology & Hepatology": ["2468-1156"], 
    "Gastroenterology": ["0016-5085", "1528-0012"],
    "Gut": ["0017-5749", "1468-3288"],
    "Hepatology": ["0270-9139", "1527-3350"],
    "Journal of Hepatology": ["0168-8278", "1600-0641"],
    "Nature Reviews Gastroenterology & Hepatology": ["1759-5045", "1759-5053"],
    "The Lancet": ["0140-6736", "1474-547X"],
    "New England Journal of Medicine": ["0028-4793", "1533-4406"],
    "Nature Medicine": ["1078-8956", "1546-170X"],
    "The BMJ": ["0959-8138", "1756-1833"] 
}

st.set_page_config(page_title="Live Impact Factor Dashboard", layout="wide", page_icon="📊")

# -----------------------------------------------------------------------------
# API FUNCTIONS
# -----------------------------------------------------------------------------

def get_scopus_denominator_dois(issn_list, year_1, year_2, api_key, status_container=None):
    """
    Fetches the list of DOIs for Articles and Reviews from Scopus.
    Returns a DataFrame of the denominator items.
    """
    url = "https://api.elsevier.com/content/search/scopus"
    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json"
    }
    
    # Construct ISSN query: (ISSN(A) OR ISSN(B))
    issn_query = " OR ".join([f"ISSN({x})" for x in issn_list])
    
    # Correct Year Logic: inclusive range
    query = f"({issn_query}) AND PUBYEAR > {min(year_1, year_2) - 1} AND PUBYEAR < {max(year_1, year_2) + 1} AND (DOCTYPE(ar) OR DOCTYPE(re))"
    
    all_docs = []
    cursor = 0
    batch_size = 25 # Scopus Search API default limit per page
    
    # First call to get total count
    try:
        params = {
            "query": query,
            "count": batch_size,
            "start": cursor,
            "field": "dc:identifier,dc:title,prism:doi,prism:coverDate,citedby-count"
        }
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        search_results = data.get('search-results', {})
        total_results = int(search_results.get('opensearch:totalResults', 0))
        
        if total_results == 0:
            return pd.DataFrame()

        entries = search_results.get('entry', [])
        all_docs.extend(entries)
        
        # Pagination
        while len(all_docs) < total_results:
            cursor += batch_size
            if status_container:
                status_container.text(f"Fetching Scopus Denominator: {len(all_docs)} / {total_results} items...")
            
            params['start'] = cursor
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                break
            
            new_entries = r.json().get('search-results', {}).get('entry', [])
            if not new_entries:
                break
            all_docs.extend(new_entries)
            time.sleep(0.2) # Throttle slightly
            
    except Exception as e:
        st.error(f"Scopus API Error: {e}")
        return pd.DataFrame()

    # Process into clean dataframe
    clean_data = []
    for doc in all_docs:
        clean_data.append({
            "doi": doc.get('prism:doi'),
            "title": doc.get('dc:title'),
            "date": doc.get('prism:coverDate'),
            "scopus_id": doc.get('dc:identifier'),
            "total_citations_lifetime": int(doc.get('citedby-count', 0)) # Lifetime, not year-specific
        })
    
    df = pd.DataFrame(clean_data)
    # Remove entries without DOIs as they can't be linked
    df = df.dropna(subset=['doi'])
    return df

def get_openalex_citations_batch(doi_list, target_year, status_container=None):
    """
    Takes a list of DOIs, queries OpenAlex in batches, 
    and returns a mapping {doi: citations_in_target_year}.
    """
    base_url = "https://api.openalex.org/works"
    doi_citation_map = {doi: 0 for doi in doi_list}
    
    # OpenAlex allows filtering by multiple DOIs (doi:A|doi:B). 
    # Limit is roughly 50-100 DOIs per URL length.
    batch_size = 40
    chunks = [doi_list[i:i + batch_size] for i in range(0, len(doi_list), batch_size)]
    
    total_chunks = len(chunks)
    
    for i, chunk in enumerate(chunks):
        if status_container:
            status_container.text(f"Fetching OpenAlex Citations: Batch {i+1}/{total_chunks}...")
            
        doi_filter = "|".join([f"doi:{doi}" for doi in chunk])
        filter_param = f"doi:{doi_filter}"
        
        params = {
            "filter": filter_param,
            "per_page": 100,
            "select": "doi,counts_by_year"
        }
        
        try:
            r = requests.get(base_url, params=params, timeout=10)
            if r.status_code == 429:
                time.sleep(1)
                continue
            
            data = r.json()
            results = data.get('results', [])
            
            for work in results:
                # OpenAlex DOI format: https://doi.org/10.1016/...
                # Scopus DOI format: 10.1016/...
                oa_doi = work.get('doi', '').replace("https://doi.org/", "")
                
                cby = work.get('counts_by_year', [])
                count = 0
                for entry in cby:
                    if entry.get('year') == target_year:
                        count = entry.get('cited_by_count', 0)
                        break
                
                if oa_doi in doi_citation_map:
                    doi_citation_map[oa_doi] = count
                    
        except Exception:
            pass # Skip failed batches to keep moving
            
        time.sleep(0.1)
        
    return doi_citation_map

def get_scopus_citations_heuristic(issn_list, journal_name, target_year, api_key):
    """
    Estimates 'Pure Scopus' citations using a Reference Search (REF).
    Note: accurate searching for citations by year in Scopus requires 'Citation Overview' API (higher tier).
    This uses the 'Search' API to find papers in target_year that mention the journal in references.
    """
    url = "https://api.elsevier.com/content/search/scopus"
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    
    # We search for papers published in target_year that reference the journal title
    # Removing 'The' can sometimes help REF matching in Scopus
    search_title = journal_name.replace("The ", "").split(":")[0]
    
    # Query: REF(Journal Name) AND PUBYEAR = Target
    query = f'REF("{search_title}") AND PUBYEAR = {target_year}'
    
    params = {"query": query, "count": 0}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        data = r.json()
        return int(data.get('search-results', {}).get('opensearch:totalResults', 0))
    except:
        return 0

# -----------------------------------------------------------------------------
# UI & LOGIC
# -----------------------------------------------------------------------------

st.title("Live Impact Factor Dashboard 📊")
st.markdown("Use the tabs below to switch between the Summary View and the Audit Data inspector.")

# Session State for storing heavy data
if 'journal_data' not in st.session_state:
    st.session_state['journal_data'] = {}

with st.sidebar:
    st.header("Configuration")
    scopus_key = st.text_input("Scopus API Key", type="password")
    
    current_date = datetime.date.today()
    target_year = st.number_input("Target Citation Year", value=current_date.year - 1)
    
    st.info(f"Numerator: Citations in **{target_year}**\nDenominator: Papers in **{target_year-1}** & **{target_year-2}**")
    
    mode = st.radio("Analysis Mode", ["Hybrid (Scopus Docs + OpenAlex Cites)", "Pure Scopus (Heuristic)"])
    
    run_btn = st.button("Run Analysis", type="primary")

tab1, tab2 = st.tabs(["🏆 Dashboard", "🔍 Data Inspector"])

if run_btn and scopus_key:
    # Clear previous results
    st.session_state['journal_data'] = {}
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    results_summary = []
    
    for i, (j_name, j_issns) in enumerate(JOURNALS.items()):
        status_text.markdown(f"**Processing:** {j_name}")
        
        # 1. Fetch Denominator (List of actual papers)
        denom_df = get_scopus_denominator_dois(j_issns, target_year-1, target_year-2, scopus_key, status_text)
        
        denom_count = len(denom_df)
        numerator_count = 0
        
        if denom_count > 0:
            # 2. Fetch Numerator based on Mode
            if mode.startswith("Hybrid"):
                # Pass the exact DOIs to OpenAlex
                doi_list = denom_df['doi'].tolist()
                citations_map = get_openalex_citations_batch(doi_list, target_year, status_text)
                
                # Map citations back to the dataframe for auditing
                denom_df['citations_target_year'] = denom_df['doi'].map(citations_map).fillna(0)
                numerator_count = denom_df['citations_target_year'].sum()
                
            else:
                # Pure Scopus
                # For audit, we can't easily map back to individual papers without CitOverview API
                # So we use the heuristic total
                numerator_count = get_scopus_citations_heuristic(j_issns, j_name, target_year, scopus_key)
                denom_df['citations_target_year'] = "N/A (Aggregated)" 
        
        # Calculate IF
        if_val = numerator_count / denom_count if denom_count > 0 else 0
        
        # Store detailed data for Audit Tab
        st.session_state['journal_data'][j_name] = {
            "df": denom_df,
            "numerator": numerator_count,
            "denominator": denom_count,
            "if": if_val
        }
        
        results_summary.append({
            "Journal": j_name,
            "IF": if_val,
            "Citations": numerator_count,
            "Articles/Reviews": denom_count
        })
        
        progress_bar.progress((i + 1) / len(JOURNALS))
        
    status_text.success("Analysis Complete!")
    time.sleep(1)
    status_text.empty()
    progress_bar.empty()

# -----------------------------------------------------------------------------
# TAB 1: DASHBOARD
# -----------------------------------------------------------------------------
with tab1:
    if st.session_state['journal_data']:
        # Create Summary DF from session state
        summary_data = []
        for name, data in st.session_state['journal_data'].items():
            summary_data.append({
                "Journal": name,
                "Live IF": data['if'],
                "Numerator": data['numerator'],
                "Denominator": data['denominator']
            })
        
        df_sum = pd.DataFrame(summary_data).sort_values("Live IF", ascending=False)
        
        col1, col2 = st.columns([3, 2])
        
        with col1:
            st.subheader("Rankings")
            fig = px.bar(df_sum, x="Live IF", y="Journal", orientation='h', text_auto='.3f',
                         color="Live IF", title=f"projected Impact Factor ({target_year})")
            fig.update_layout(yaxis={'categoryorder':'total ascending'}, height=600)
            st.plotly_chart(fig, use_container_width=True)
            
        with col2:
            st.subheader("Summary Table")
            st.dataframe(df_sum.style.format({"Live IF": "{:.3f}", "Numerator": "{:,.0f}", "Denominator": "{:,.0f}"}), use_container_width=True)
            
            st.caption(f"**Methodology:** {mode}")
            if mode.startswith("Pure"):
                st.warning("⚠️ Pure Scopus Mode uses a 'References Search' heuristic. It is less accurate than Hybrid mode without an advanced Scopus subscription.")
    else:
        st.info("Run the analysis to see results.")

# -----------------------------------------------------------------------------
# TAB 2: AUDIT / DATA INSPECTOR
# -----------------------------------------------------------------------------
with tab2:
    st.header("Data Inspector")
    st.markdown("Select a journal to see exactly which papers are included in the denominator and how many citations they contributed.")
    
    if st.session_state['journal_data']:
        selected_journal = st.selectbox("Select Journal", list(st.session_state['journal_data'].keys()))
        
        j_data = st.session_state['journal_data'][selected_journal]
        j_df = j_data['df']
        
        if j_df.empty:
            st.warning("No documents found for this journal in Scopus.")
        else:
            col_metrics1, col_metrics2 = st.columns(2)
            col_metrics1.metric("Total Denominator Items", j_data['denominator'])
            col_metrics2.metric("Total Numerator Citations", f"{j_data['numerator']:,.0f}")
            
            st.subheader("Denominator Documents (Scopus)")
            
            # Sort by highest contributors if Hybrid
            if 'citations_target_year' in j_df.columns and not isinstance(j_df.iloc[0]['citations_target_year'], str):
                j_df = j_df.sort_values("citations_target_year", ascending=False)
            
            st.dataframe(
                j_df, 
                column_config={
                    "doi": "DOI",
                    "title": "Article Title",
                    "date": "Pub Date",
                    "citations_target_year": st.column_config.NumberColumn(
                        f"Citations in {target_year}",
                        help=f"Citations received in {target_year} (Source: OpenAlex)"
                    ),
                    "total_citations_lifetime": "Total Lifetime Cites"
                },
                use_container_width=True,
                height=600
            )
            
            st.download_button(
                "Download Audit CSV",
                j_df.to_csv(index=False).encode('utf-8'),
                f"{selected_journal}_audit.csv",
                "text/csv"
            )
    else:
        st.info("Run the analysis first to inspect data.")
