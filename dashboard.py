import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import re
from datetime import datetime
import xml.etree.ElementTree as ET

# --- CONFIGURATION ---
CURRENT_YEAR = datetime.now().year
TARGET_YEARS = [CURRENT_YEAR - 1, CURRENT_YEAR - 2]
MIN_PAGE_COUNT = 3  # Articles must be at least this many pages to count

JOURNALS = {
    "Lancet Gastro & Hep": "2468-1253",
    "Nat Rev Gastro & Hep": "1759-5053",
    "J. Hepatology": "0168-8278",
    "Gastroenterology": "0016-5085",
    "Gut": "0017-5749",
    "Hepatology": "0270-9139"
}

# --- HELPER FUNCTIONS ---

def calculate_page_count(biblio):
    """
    Robustly calculates page count from OpenAlex biblio data.
    Handles 'e100-e105' and '123-125' formats.
    """
    if not biblio:
        return 0
    
    first = biblio.get('first_page')
    last = biblio.get('last_page')
    
    if not first or not last:
        return 0
        
    # Remove non-numeric characters (e.g., 'e', 'S')
    def clean_page(p):
        return re.sub(r'[^\d]', '', str(p))
    
    try:
        f_val = int(clean_page(first))
        l_val = int(clean_page(last))
        
        # Handle cases where pages are inverted or identical
        count = abs(l_val - f_val) + 1
        
        # Sanity check: If count is > 1000, it's likely a parsing error or dataset issue
        if count > 1000: 
            return 0
        return count
    except ValueError:
        return 0

def check_pubmed_types(dois):
    """
    Uses PubMed API (E-utilities) to get the OFFICIAL publication type.
    This is the most reliable method but requires a separate API call.
    """
    if not dois:
        return {}
    
    # 1. Search for PMIDs using DOIs
    base_search = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    # Join DOIs with OR
    query = " OR ".join([f"{doi}[Location ID]" for doi in dois])
    
    try:
        r = requests.get(base_search, params={"db": "pubmed", "term": query, "retmode": "json"})
        data = r.json()
        pmids = data.get("esearchresult", {}).get("idlist", [])
        
        if not pmids:
            return {}

        # 2. Fetch details for these PMIDs
        base_fetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        r_fetch = requests.get(base_fetch, params={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"})
        
        # Parse XML
        root = ET.fromstring(r_fetch.content)
        results = {}
        
        for article in root.findall(".//PubmedArticle"):
            # Get DOI to map back
            doi_elem = article.find(".//ArticleId[@IdType='doi']")
            if doi_elem is None: continue
            doi = "https://doi.org/" + doi_elem.text
            
            # Get Types
            types = [t.text for t in article.findall(".//PublicationType")]
            results[doi] = types
            
        return results
        
    except Exception as e:
        st.error(f"PubMed API Error: {e}")
        return {}

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
            # We explicitly ask for 'biblio' and 'doi' now
            "select": "id,title,publication_year,type,counts_by_year,primary_topic,cited_by_count,biblio,doi"
        }
        
        try:
            r = requests.get(base_url, params=params)
            r.raise_for_status()
            data = r.json()
            works.extend(data.get('results', []))
            cursor = data['meta'].get('next_cursor')
            if not data.get('results'): break
        except:
            break

    processed_data = []
    
    for work in works:
        title = work.get('title') or "Untitled"
        biblio = work.get('biblio', {})
        
        # 1. PAGE COUNT CALCULATION
        pages = calculate_page_count(biblio)
        
        citations_this_year = 0
        if work.get('counts_by_year'):
            for count in work['counts_by_year']:
                if count['year'] == CURRENT_YEAR:
                    citations_this_year = count['cited_by_count']
                    break
        
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
            "Total_Citations": work['cited_by_count'],
            "Pages": pages,
            "DOI": work.get('doi'),
            "Biblio_Raw": f"{biblio.get('first_page')}-{biblio.get('last_page')}"
        })
        
    return pd.DataFrame(processed_data)

# --- APP UI ---

st.set_page_config(page_title="Live IF Tracker (Deep Clean)", layout="wide")
st.title(f"Live IF Tracker: Deep Clean Edition ({CURRENT_YEAR})")

# Sidebar Controls
st.sidebar.header("Filter Settings")
min_pages = st.sidebar.slider("Minimum Page Count", 0, 10, 3, help="Papers with fewer pages than this are excluded (likely abstracts/letters).")

# Fetch Data
if 'data' not in st.session_state:
    st.session_state.data = pd.DataFrame()

if st.button("Fetch/Refresh Data"):
    all_data = []
    prog = st.progress(0)
    for i, (name, issn) in enumerate(JOURNALS.items()):
        all_data.append(fetch_journal_data(name, issn))
        prog.progress((i+1)/len(JOURNALS))
    st.session_state.data = pd.concat(all_data, ignore_index=True)
    prog.empty()

if st.session_state.data.empty:
    st.info("Click 'Fetch Data' to start.")
    st.stop()

# APPLY FILTERS
df = st.session_state.data.copy()

# The "Page Count" Filter
clean_df = df[df["Pages"] >= min_pages]
excluded_df = df[df["Pages"] < min_pages]

# --- METRICS ---
st.subheader("Impact Factor Estimation")

metrics = []
for journal in JOURNALS.keys():
    j_df = clean_df[clean_df["Journal"] == journal]
    num = j_df["Citations_Current_Year"].sum()
    den = len(j_df)
    est_if = num / den if den > 0 else 0
    
    # Calculate how many were removed
    orig_count = len(df[df["Journal"] == journal])
    removed = orig_count - den
    
    metrics.append({
        "Journal": journal,
        "Live IF": round(est_if, 2),
        "Citable Items": den,
        "Items Removed (Short)": removed
    })

m_df = pd.DataFrame(metrics).sort_values("Live IF", ascending=False)
st.dataframe(m_df.style.highlight_max(subset=["Live IF"]), hide_index=True)

# --- VISUALS ---
col1, col2 = st.columns(2)
with col1:
    fig = px.bar(m_df, x="Journal", y="Live IF", color="Journal", title="Live IF (Cleaned)")
    st.plotly_chart(fig, use_container_width=True)
with col2:
    # Histogram of PAGE COUNTS
    fig_pg = px.histogram(df, x="Pages", color="Journal", barmode="overlay", range_x=[0, 20], nbins=20, 
                          title="Distribution of Page Counts (Spot the Abstracts!)")
    fig_pg.add_vline(x=min_pages, line_dash="dash", annotation_text="Cutoff")
    st.plotly_chart(fig_pg, use_container_width=True)

# --- PUBMED VALIDATOR ---
st.divider()
st.subheader("PubMed Validator (The Truth Serum)")
st.markdown("Select a journal to cross-reference the top 5 'Articles' with PubMed to see what they *really* are.")

check_journal = st.selectbox("Select Journal", list(JOURNALS.keys()))
# Get top 5 cited papers from the "clean" list
candidates = clean_df[clean_df["Journal"] == check_journal].sort_values("Citations_Current_Year", ascending=False).head(5)

if st.button("Check Top 5 Papers in PubMed"):
    dois = candidates["DOI"].dropna().tolist()
    if dois:
        pubmed_types = check_pubmed_types(dois)
        
        # Display results
        results = []
        for _, row in candidates.iterrows():
            ptypes = pubmed_types.get(row["DOI"], ["Not Found"])
            results.append({
                "Title": row["Title"],
                "OpenAlex Type": row["Type"],
                "PubMed Type": ", ".join(ptypes),
                "Pages": row["Pages"]
            })
        st.table(pd.DataFrame(results))
    else:
        st.warning("No DOIs found for these papers.")

# --- DATA EXPLORER ---
st.divider()
st.subheader("Excluded Papers Inspector")
st.write(f"Showing papers with < {min_pages} pages (Presumed Abstracts/Letters)")
st.dataframe(excluded_df[["Journal", "Title", "Pages", "Biblio_Raw", "Citations_Current_Year"]].sort_values("Citations_Current_Year", ascending=False))
