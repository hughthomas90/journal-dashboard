import streamlit as st
import pandas as pd
import requests
import datetime
import time
import plotly.express as px

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------

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

def get_scopus_documents(issn_list, year_1, year_2, api_key, only_citable=False, status_container=None):
    """
    Fetches DOIs from Scopus.
    if only_citable=True: Adds (DOCTYPE(ar) OR DOCTYPE(re)) -> For Denominator
    if only_citable=False: Fetches EVERYTHING -> For Numerator Source
    """
    url = "https://api.elsevier.com/content/search/scopus"
    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json"
    }
    
    # Construct ISSN query
    issn_query = " OR ".join([f"ISSN({x})" for x in issn_list])
    
    # Base Query: Years + ISSN
    # range is inclusive in our logic below, but Scopus integer logic needs explicit bounds
    # e.g. for 2023-2024: PUBYEAR > 2022 AND PUBYEAR < 2025
    query = f"({issn_query}) AND PUBYEAR > {min(year_1, year_2) - 1} AND PUBYEAR < {max(year_1, year_2) + 1}"
    
    if only_citable:
        query += " AND (DOCTYPE(ar) OR DOCTYPE(re))"
    
    all_docs = []
    cursor = 0
    batch_size = 25
    
    try:
        # Initial call to get counts
        params = {
            "query": query,
            "count": batch_size,
            "start": cursor,
            "field": "dc:identifier,dc:title,prism:doi,prism:coverDate,prism:aggregationType,subtypeDescription" 
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
        
        label = "Citable Items (Denom)" if only_citable else "All Content (Num Source)"
        
        # Pagination
        while len(all_docs) < total_results:
            cursor += batch_size
            if status_container:
                status_container.text(f"Fetching {label}: {len(all_docs)} / {total_results}...")
            
            params['start'] = cursor
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                break
            
            new_entries = r.json().get('search-results', {}).get('entry', [])
            if not new_entries:
                break
            all_docs.extend(new_entries)
            time.sleep(0.1) # Mild throttle
            
    except Exception as e:
        st.error(f"Scopus API Error: {e}")
        return pd.DataFrame()

    clean_data = []
    for doc in all_docs:
        clean_data.append({
            "doi": doc.get('prism:doi'),
            "title": doc.get('dc:title'),
            "date": doc.get('prism:coverDate'),
            "type": doc.get('subtypeDescription'),
            "scopus_id": doc.get('dc:identifier')
        })
    
    df = pd.DataFrame(clean_data)
    # We need DOIs to link to OpenAlex. 
    # Items without DOIs (rare in Scopus for these journals) cannot be counted in numerator easily.
    df = df.dropna(subset=['doi'])
    return df

def get_openalex_citations_batch(doi_list, target_year, status_container=None):
    """
    Queries OpenAlex for the citation count specifically in 'target_year'
    for a list of DOIs.
    """
    base_url = "https://api.openalex.org/works"
    doi_citation_map = {doi: 0 for doi in doi_list}
    
    # Chunking
    batch_size = 40
    chunks = [doi_list[i:i + batch_size] for i in range(0, len(doi_list), batch_size)]
    total_chunks = len(chunks)
    
    for i, chunk in enumerate(chunks):
        if status_container:
            status_container.text(f"Counting Citations in {target_year}: Batch {i+1}/{total_chunks}...")
            
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
                # OpenAlex returns https://doi.org/10.1016/... 
                # Scopus returns 10.1016/...
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
            pass 
        time.sleep(0.05)
        
    return doi_citation_map

# -----------------------------------------------------------------------------
# UI & LOGIC
# -----------------------------------------------------------------------------

st.title("Live Impact Factor Dashboard 📊")

if 'audit_data' not in st.session_state:
    st.session_state['audit_data'] = {}

with st.sidebar:
    st.header("Settings")
    scopus_key = st.text_input("Scopus API Key", type="password")
    
    # Date Logic
    today = datetime.date.today()
    default_target = today.year 
    
    target_year = st.number_input("Impact Factor Year (Numerator)", value=default_target, 
                                  help="The year in which citations are counted. e.g., For 2025 IF, select 2025.")
    
    # Denom years are always Target-1 and Target-2
    y1 = target_year - 1
    y2 = target_year - 2
    
    st.markdown(f"""
    **Formula Logic:**
    * **Numerator:** Citations in `{target_year}` to *ALL* content published in `{y1}` & `{y2}`.
    * **Denominator:** Count of *Articles* & *Reviews* published in `{y1}` & `{y2}`.
    """)
    
    run_btn = st.button("Calculate", type="primary")

tab_dash, tab_audit = st.tabs(["Dashboard", "Data Inspector"])

if run_btn and scopus_key:
    # Reset
    st.session_state['audit_data'] = {}
    
    status = st.empty()
    progress = st.progress(0)
    summary_rows = []
    
    total_journals = len(JOURNALS)
    
    for idx, (name, issns) in enumerate(JOURNALS.items()):
        status.markdown(f"**Processing:** {name}")
        
        # 1. Denominator: Fetch Scopus Articles + Reviews (Strict)
        denom_df = get_scopus_documents(issns, y1, y2, scopus_key, only_citable=True, status_container=status)
        denom_count = len(denom_df)
        
        # 2. Numerator Source: Fetch Scopus ALL Docs (Broad)
        # We need this list to query OpenAlex for citations to "non-citable" items too (Editorials etc)
        # If denom_count is 0, we skip, but actually we should check if there are non-citable items? 
        # Usually if denom is 0, journal might be wrong. But let's try fetch.
        all_docs_df = get_scopus_documents(issns, y1, y2, scopus_key, only_citable=False, status_container=status)
        
        if not all_docs_df.empty:
            # 3. Get Citations from OpenAlex for ALL docs
            all_dois = all_docs_df['doi'].tolist()
            cit_map = get_openalex_citations_batch(all_dois, target_year, status_container=status)
            
            # Map back
            all_docs_df['citations_target'] = all_docs_df['doi'].map(cit_map).fillna(0)
            numerator_count = all_docs_df['citations_target'].sum()
        else:
            numerator_count = 0
            
        # Calculate IF
        if_val = numerator_count / denom_count if denom_count > 0 else 0
        
        summary_rows.append({
            "Journal": name,
            "Live IF": if_val,
            "Numerator (Citations)": numerator_count,
            "Denominator (Articles/Reviews)": denom_count
        })
        
        # Store for Audit
        st.session_state['audit_data'][name] = {
            "denom_df": denom_df,
            "all_docs_df": all_docs_df
        }
        
        progress.progress((idx + 1) / total_journals)

    status.success("Done!")
    time.sleep(1)
    status.empty()
    progress.empty()
    
    # Store summary in session state for persistence
    st.session_state['summary_df'] = pd.DataFrame(summary_rows).sort_values("Live IF", ascending=False)


# -----------------------------------------------------------------------------
# TAB 1: DASHBOARD
# -----------------------------------------------------------------------------
with tab_dash:
    if 'summary_df' in st.session_state:
        df = st.session_state['summary_df']
        
        c1, c2 = st.columns([3, 2])
        
        with c1:
            st.subheader(f"Projected Impact Factor ({target_year})")
            fig = px.bar(df, x="Live IF", y="Journal", orientation='h', text_auto='.3f',
                         color="Live IF", color_continuous_scale="Viridis")
            fig.update_layout(yaxis={'categoryorder':'total ascending'}, height=600)
            st.plotly_chart(fig, use_container_width=True)
            
        with c2:
            st.subheader("Metrics")
            st.dataframe(
                df.style.format({
                    "Live IF": "{:.3f}", 
                    "Numerator (Citations)": "{:,.0f}", 
                    "Denominator (Articles/Reviews)": "{:,.0f}"
                }), 
                use_container_width=True
            )
    else:
        st.info("Enter Scopus API Key and click Calculate.")

# -----------------------------------------------------------------------------
# TAB 2: AUDIT
# -----------------------------------------------------------------------------
with tab_audit:
    if 'audit_data' in st.session_state and st.session_state['audit_data']:
        selected = st.selectbox("Select Journal", list(st.session_state['audit_data'].keys()))
        data = st.session_state['audit_data'][selected]
        
        st.write(f"### {selected}")
        
        # Audit Numerator
        st.markdown("#### Numerator Contributors (All Content)")
        st.caption(f"Showing citations received in {target_year} to all documents published in {y1}-{y2}.")
        
        num_df = data['all_docs_df'].copy()
        if 'citations_target' in num_df.columns:
            num_df = num_df.sort_values("citations_target", ascending=False)
            
            # Flag if it's in denominator
            denom_dois = set(data['denom_df']['doi'].tolist())
            num_df['is_citable'] = num_df['doi'].apply(lambda x: "✅ Yes" if x in denom_dois else "❌ No")
            
            st.dataframe(
                num_df[['doi', 'citations_target', 'is_citable', 'type', 'title', 'date']],
                column_config={
                    "citations_target": st.column_config.NumberColumn(f"Citations ({target_year})", format="%d"),
                    "is_citable": "In Denom?",
                    "type": "Scopus Type"
                },
                use_container_width=True
            )
            
            st.metric("Total Numerator Citations", f"{num_df['citations_target'].sum():,.0f}")
        else:
            st.warning("No data found.")
            
    else:
        st.write("Run calculation to inspect data.")
