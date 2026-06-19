import streamlit as st
import os
import pandas as pd
from google.cloud import bigquery
import google.auth

# Set page configuration for wide layout and custom title
st.set_page_config(
    page_title="Document Pipeline Dashboard",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Premium dark theme custom CSS injections
st.markdown("""
<style>
    /* Main Background and Text Colors */
    .stApp {
        background-color: #0F172A;
        color: #F8FAFC;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    
    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #1E293B !important;
        border-right: 1px solid #334155;
    }
    section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] label {
        color: #E2E8F0 !important;
    }
    
    /* Headings and Titles */
    h1, h2, h3 {
        font-weight: 700 !important;
        color: #F1F5F9 !important;
        letter-spacing: -0.025em;
    }
    .main-title {
        background: linear-gradient(90deg, #38BDF8, #818CF8);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.8rem;
        font-weight: 800;
        margin-bottom: 0.2rem;
    }
    .subtitle {
        color: #94A3B8;
        font-size: 1.1rem;
        margin-bottom: 2rem;
    }
    
    /* Styled Metric Cards */
    .metric-card {
        background: rgba(30, 41, 59, 0.7);
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 1.5rem;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
        transition: transform 0.2s, border-color 0.2s;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        border-color: #38BDF8;
    }
    .metric-label {
        font-size: 0.875rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #94A3B8;
        font-weight: 600;
    }
    .metric-value {
        font-size: 2rem;
        font-weight: 700;
        color: #F8FAFC;
        margin-top: 0.5rem;
    }
    
    /* Styled Dataframe/Table overrides */
    div[data-testid="stDataFrame"] {
        border: 1px solid #334155;
        border-radius: 8px;
        overflow: hidden;
    }
    
    /* Refresh Button Styling */
    .stButton>button {
        background: linear-gradient(135deg, #0284C7, #4F46E5) !important;
        color: white !important;
        font-weight: 600 !important;
        border: none !important;
        border-radius: 8px !important;
        padding: 0.5rem 1.5rem !important;
        box-shadow: 0 4px 6px -1px rgba(56, 189, 248, 0.2) !important;
        transition: all 0.2s !important;
    }
    .stButton>button:hover {
        background: linear-gradient(135deg, #38BDF8, #6366F1) !important;
        transform: scale(1.02) !important;
        box-shadow: 0 10px 15px -3px rgba(56, 189, 248, 0.3) !important;
    }
    
    /* Info/Warning banners */
    .stAlert {
        background-color: #1E293B !important;
        border: 1px solid #475569 !important;
        color: #F8FAFC !important;
    }
</style>
""", unsafe_allow_html=True)

# Attempt to auto-detect GCP Project ID using credentials
@st.cache_resource
def get_default_project():
    try:
        _, project = google.auth.default()
        return project
    except Exception:
        return ""

default_project = get_default_project()
if not default_project:
    default_project = os.environ.get("GCP_PROJECT", "")

# --- SIDEBAR CONFIGURATION ---
st.sidebar.markdown("### ⚙️ Pipeline Configuration")

project_id = st.sidebar.text_input(
    "Google Cloud Project ID",
    value=default_project,
    help="The ID of the GCP project hosting your BigQuery dataset."
)

dataset_id = st.sidebar.text_input(
    "BigQuery Dataset ID",
    value="document_processing",
    help="Name of the BigQuery dataset."
)

table_id = st.sidebar.text_input(
    "BigQuery Table ID",
    value="metadata",
    help="Name of the BigQuery metadata table."
)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🔍 Filter Documents")

search_query = st.sidebar.text_input("Search Filename", placeholder="Type a filename...")

# --- DATA RETRIEVAL FUNCTION ---
def fetch_data(project, dataset, table):
    if not project:
        return None, "Project ID is not specified. Please enter it in the sidebar."
        
    try:
        client = bigquery.Client(project=project)
        query = f"""
            SELECT 
                filename, 
                bucket_name, 
                word_count, 
                tags, 
                created_time, 
                processed_time 
            FROM `{project}.{dataset}.{table}` 
            ORDER BY processed_time DESC
        """
        query_job = client.query(query)
        df = query_job.to_dataframe()
        return df, None
    except Exception as e:
        return None, f"Failed to retrieve data from BigQuery:\n\n{str(e)}"

# --- MAIN CONTENT AREA ---
st.markdown('<div class="main-title">📄 Document Ingestion Pipeline</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Monitor metadata and process files serverless on Google Cloud</div>', unsafe_allow_html=True)

if not project_id:
    st.info("👈 Please enter your Google Cloud Project ID in the sidebar to load the document table.")
else:
    # Refresh button
    col_title, col_btn = st.columns([6, 1])
    with col_btn:
        refresh = st.button("🔄 Refresh Data", use_container_width=True)
        
    # Fetch Data
    with st.spinner("Connecting to Google Cloud BigQuery..."):
        df, error = fetch_data(project_id, dataset_id, table_id)

    if error:
        st.error(error)
        st.markdown("""
        **Troubleshooting Checklist:**
        1. **Authentication**: Have you run `gcloud auth application-default login` on your local machine?
        2. **Project ID**: Is the Project ID in the sidebar correct?
        3. **Dataset/Table**: Have you deployed the resources yet by running `deploy.ps1`?
        """)
    elif df is None or len(df) == 0:
        st.warning(f"No records found in BigQuery table `{project_id}.{dataset_id}.{table_id}`. Upload some files to your Cloud Storage bucket to begin ingestion!")
    else:
        # Pre-process dataframe tags (BigQuery returns them as list arrays or None)
        df['tags'] = df['tags'].apply(lambda x: list(x) if isinstance(x, (list, pd.Series)) else [])
        
        # Get all unique tags for the sidebar filter
        all_tags = sorted(list(set(tag for tags_list in df['tags'] for tag in tags_list)))
        selected_tags = st.sidebar.multiselect("Filter by Tags", options=all_tags)

        # Apply Filters
        filtered_df = df.copy()
        
        # Tag Filter
        if selected_tags:
            filtered_df = filtered_df[filtered_df['tags'].apply(lambda x: any(tag in x for tag in selected_tags))]
            
        # Filename Search Filter
        if search_query:
            filtered_df = filtered_df[filtered_df['filename'].str.contains(search_query, case=False)]

        # --- METRICS PANEL ---
        m1, m2, m3 = st.columns(3)
        
        total_docs = len(filtered_df)
        avg_words = int(filtered_df['word_count'].mean()) if total_docs > 0 else 0
        unique_tags_count = len(set(tag for tags_list in filtered_df['tags'] for tag in tags_list))

        with m1:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">Total Documents</div>
                <div class="metric-value">{total_docs}</div>
            </div>
            """, unsafe_allow_html=True)
            
        with m2:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">Avg Word Count</div>
                <div class="metric-value">{avg_words:,}</div>
            </div>
            """, unsafe_allow_html=True)
            
        with m3:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">Active Unique Tags</div>
                <div class="metric-value">{unique_tags_count}</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # --- DATA TABLE ---
        st.markdown("### 📊 Ingested Document Metadata")
        
        # Format the tags column as comma-separated text for clean display in st.dataframe
        display_df = filtered_df.copy()
        display_df['tags'] = display_df['tags'].apply(lambda x: ", ".join(x) if x else "None")
        
        # Rename columns for presentation
        display_df = display_df.rename(columns={
            "filename": "Filename",
            "bucket_name": "GCS Bucket",
            "word_count": "Word Count",
            "tags": "Extracted Tags",
            "created_time": "GCS Upload Date",
            "processed_time": "Processing Date"
        })
        
        # Format dates for readability
        display_df['GCS Upload Date'] = pd.to_datetime(display_df['GCS Upload Date']).dt.strftime('%Y-%m-%d %H:%M:%S')
        display_df['Processing Date'] = pd.to_datetime(display_df['Processing Date']).dt.strftime('%Y-%m-%d %H:%M:%S')

        # Display Dataframe
        st.dataframe(
            display_df[["Filename", "Word Count", "Extracted Tags", "GCS Upload Date", "Processing Date", "GCS Bucket"]],
            use_container_width=True,
            hide_index=True
        )

        # Download CSV option
        csv = display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Export Metadata CSV",
            data=csv,
            file_name='document_metadata.csv',
            mime='text/csv',
        )
