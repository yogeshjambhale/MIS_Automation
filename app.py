import streamlit as st
import pandas as pd
import numpy as np
import io

# --- 1. The Processing Logic ---
def process_car_rental_mis(file):
    # Read the uploaded CSV file
    df = pd.read_csv(file)
    
    # --- COLUMN MERGING ---
    columns_to_merge = {
        'Emp ID': ['Emp ID', 'EMP ID', 'EMP CODE', 'EMP. CODE', 'EMP .CODE', 'Employee ID', 'Emp_ID', 'Employee_ID'],
        'Cost Center': ['Cost Center', 'COST CENTER', 'Cost Centre', 'Cost_Center'],
        'GSTN Number': ['GSTN Number', 'GSTN NUMBER', 'GSTIN NUMBER', 'GSTIN', 'GST_Number'],
        'TRAVEL ID': ['TRAVEL ID', 'Travel id', 'Travel_ID', 'Travel ID'],
        'Trip id': ['Trip id', 'TRIP ID', 'Trip_ID', 'Trip ID']
    }

    for target_col, variations in columns_to_merge.items():
        existing_cols = [col for col in variations if col in df.columns]
        if existing_cols:
            df[target_col] = df[existing_cols].bfill(axis=1).iloc[:, 0]
            cols_to_drop = [c for c in existing_cols if c != target_col]
            df.drop(columns=cols_to_drop, inplace=True, errors='ignore')

    # --- FILTERING ---
    if 'Trip Status' in df.columns:
        df = df[df['Trip Status'].str.upper() != 'CANCELLED']

    if 'Labels' in df.columns:
        df['Labels'] = df['Labels'].fillna('')
        unwanted_labels = 'No Bill|Pickup Fail|Duplicate Booking|Vendor No-show'
        df = df[~df['Labels'].str.contains(unwanted_labels, case=False, regex=True)]

    # --- CLASSIFICATION ---
    conditions = [
        (df['Sales Duty Slip Status'] == 'APPROVED') & (df['Sales Billing Status'] == 'BILLED'),
        (df['Sales Duty Slip Status'] == 'APPROVED') & (df['Sales Billing Status'] == 'UNBILLED'),
        (df['Sales Duty Slip Status'] == 'CREATED'),
        (df['Sales Duty Slip Status'] == 'EDITED'),
        (df['Sales Duty Slip Status'] == 'PENDING') & (df['Purchase Duty Slip Status'].isin(['APPROVED', 'CREATED', 'EDITED', 'REJECTED'])),
        (df['Sales Duty Slip Status'] == 'PENDING') & (df['Purchase Duty Slip Status'] == 'PENDING')
    ]
    
    choices = ['BILLED', 'UNBILLED', 'RECEIVED', 'RECEIVED', 'RECEIVED', 'PENDING']
    df['Final Status'] = np.select(conditions, choices, default='OTHER')

    # --- ZONAL CLASSIFICATION ---
    zone_mapping = {
        'Maharashtra': 'West', 'Gujarat': 'West', 'Goa': 'West', 'Dadra and Nagar Haveli': 'West', 'Daman and Diu': 'West',
        'Karnataka': 'South', 'Tamil Nadu': 'South', 'Kerala': 'South', 'Andhra Pradesh': 'South', 'Telangana': 'South',
        'Delhi': 'North', 'Haryana': 'North', 'Punjab': 'North', 'Uttar Pradesh': 'North', 'Rajasthan': 'North', 'Uttarakhand': 'North', 'Himachal Pradesh': 'North', 'Jammu & Kashmir': 'North', 'Chandigarh': 'North',
        'West Bengal': 'East', 'Bihar': 'East', 'Odisha': 'East', 'Jharkhand': 'East', 'Assam': 'East', 'Sikkim': 'East', 'Meghalaya': 'East', 'Tripura': 'East', 'Chhattisgarh': 'East'
    }

    if 'Pickup State' in df.columns:
        df['Pickup State'] = df['Pickup State'].str.strip().str.title()
        df['Zone'] = df['Pickup State'].map(zone_mapping).fillna('Unknown Zone')

    return df


# --- 2. The Streamlit User Interface ---
st.set_page_config(page_title="MIS Automation Tool", layout="wide")

st.title("🚗 Car Rental MIS Automation By Yogesh Jambhale")
st.markdown("Upload your raw Admin Level Report CSV to automatically merge columns, filter unwanted rows, and classify trip statuses and zones.")

# File Uploader
uploaded_file = st.file_uploader("Upload Raw Data (CSV)", type=["csv"])

if uploaded_file is not None:
    st.info("File uploaded successfully! Processing data...")
    
    try:
        # Process the data using our function
        processed_df = process_car_rental_mis(uploaded_file)
        
        st.success("✅ Data processed successfully!")
        
        # Display a preview of the cleaned data
        st.subheader("Data Preview")
        st.dataframe(processed_df.head(10))
        
        # Convert the processed dataframe back to CSV for download
        csv_buffer = io.StringIO()
        processed_df.to_csv(csv_buffer, index=False)
        csv_data = csv_buffer.getvalue()
        
        # Download Button
        st.download_button(
            label="⬇️ Download Processed MIS Report",
            data=csv_data,
            file_name="MIS_Processed_Report.csv",
            mime="text/csv"
        )
        
    except Exception as e:
        st.error(f"An error occurred during processing: {e}")