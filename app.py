import streamlit as st
import pandas as pd
import numpy as np
import io
from datetime import datetime

# --- SETTINGS & CONFIG ---
st.set_page_config(page_title="Car Rental Automation Tool", layout="wide")

# --- SHARED UTILITY: COLUMN MERGING ---
def consolidate_columns(df):
    """
    Cleans headers and merges columns based on the user's specific mappings.
    Prevents duplicates caused by extra spaces.
    """
    # Strip spaces from column names to handle 'Emp ID' vs 'Emp  ID'
    df.columns = df.columns.str.strip()
    
    mapping = {
        'Trip ID': ['Trip id', 'TRIP ID', 'Trip ID'],
        'Emp ID': ['Emp ID', 'EMP CODE', 'EMP ID', 'EMP .CODE', 'Employee ID', 'EMP. CODE'],
        'GSTN Number': ['GSTN Number', 'GSTN NUMBER', 'GST Number', 'GSTIN NUMBER'],
        'Travel ID': ['TRAVEL ID', 'Travel ID', 'Travel id'],
        'Cost Center': ['Cost Center', 'COST CENTER']
    }

    for target_col, variations in mapping.items():
        # Find which variations exist in the current dataframe
        existing_cols = [col for col in variations if col in df.columns]
        if existing_cols:
            # Consolidate values (bfill) and create/update the target column
            df[target_col] = df[existing_cols].bfill(axis=1).iloc[:, 0]
            # Remove the now-redundant variations
            cols_to_drop = [c for c in existing_cols if c != target_col]
            df.drop(columns=cols_to_drop, inplace=True, errors='ignore')
    return df

# --- 1. MIS AUTOMATION LOGIC (Retaining all features from original app.py) ---
def process_car_rental_mis(file):
    df = pd.read_csv(file)
    
    # Column Merging for MIS
    df = consolidate_columns(df)

    # Filtering
    if 'Trip Status' in df.columns:
        df = df[df['Trip Status'].str.upper() != 'CANCELLED']

    if 'Labels' in df.columns:
        df['Labels'] = df['Labels'].fillna('')
        unwanted_labels = 'No Bill|Pickup Fail|Duplicate Booking|Vendor No-show'
        df = df[~df['Labels'].str.contains(unwanted_labels, case=False, regex=True)]

    # Classification
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

    # Zonal Classification
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

# --- 2. BDC AUTOMATION LOGIC ---
def process_bdc_automation(file):
    df = pd.read_csv(file)
    df = consolidate_columns(df)

    # Time Duration Logic
    def get_duration(row):
        start = str(row.get('Trip Start Time', '')).strip()
        end = str(row.get('Trip End Time', '')).strip()
        if not start or not end or 'nan' in start.lower(): return 0.0
        try:
            fmt = '%H:%M' if len(start.split(':')) == 2 else '%H:%M:%S'
            diff = (datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds() / 3600.0
            return diff + 24.0 if diff < 0 else diff
        except: return 0.0

    df['Total_HRS_Float'] = df.apply(get_duration, axis=1)
    df['Total HRS.'] = df['Total_HRS_Float'].apply(lambda x: f"{int(x):02d}:{int((x*60)%60):02d}:00")

    # Special Cities & Slab Logic
    special_cities = ['Mumbai Suburban District', 'Thane Subdistrict', 'Kalyan Subdistrict', 'Ulhasnagar Subdistrict', 'Bhiwandi Subdistrict', 'Vasai Subdistrict', 'Mumbai City District']
    rates = {
        'SEDAN': (289, 240, 185), 'SUV': (340, 285, 215), 'PREMIUM_SUV': (500, 415, 330), 'HATCHBACK': (200, 150, 100)
    }

    def apply_billing(row):
        is_special = (row.get('Pickup City') in special_cities) and (row.get('Duty Type') == 'Daily Rentals')
        hrs = row['Total_HRS_Float']
        vt = str(row.get('Vehicle Group', 'SEDAN')).upper()
        r1, r2, r3 = rates.get(vt, rates['SEDAN'])
        
        # Slabs calculation
        s1, s2, s3 = min(6.0, hrs), min(6.0, max(0, hrs-6.0)), max(0, hrs-12.0)
        
        if is_special:
            basic = (s1*r1) + (s2*r2) + (s3*r3)
            # Use Sales Extra Hour Rate for extra KM as requested
            ex_km_chg = max(0, row.get('Trip Distance(Duty slip-KM)', 0) - 150) * row.get('Sales Extra Hour Rate', 0)
            return pd.Series(["150 km", basic, s1, s2, s3, ex_km_chg, 0, s1*r1, s2*r2, s3*r3])
        else:
            return pd.Series([row.get('Duty Package', ''), row.get('Sales Base Price', 0), s1, s2, s3, 
                             row.get('Sales Extra KM Charges', 0), row.get('Sales Extra Hour Charges', 0), 0, 0, 0])

    cols = ['BDC_Package', 'BDC_Basic', '4-6/hrs', '6-12/hrs', '12/hrs & above', 'Ex_Kms_Chg', 'Ex_Hrs_Chg', 'S1_Rate', 'S2_Rate', 'S3_Rate']
    df[cols] = df.apply(apply_billing, axis=1)

    # Financials
    df['Revenue Amt.'] = df['BDC_Basic'] + df['Ex_Kms_Chg'] + df['Ex_Hrs_Chg']
    df['Total Amt'] = df['Revenue Amt.'] + df[['PARKING (Sales)', 'TOLL (Sales)', 'NIGHT_CHARGES (Sales)', 'PERMIT (Sales)']].fillna(0).sum(axis=1)
    df['GST/IGST @ 5%'] = df['Total Amt'] * 0.05
    df['Gross Amt'] = (df['Total Amt'] + df['GST/IGST @ 5%']).round(2)

    return df

# --- STREAMLIT UI ---
st.title("🚗 Car Rental Automation Suite")
st.markdown("Automate your MIS and BDC reports with custom logic by Yogesh Jambhale.")

tab1, tab2 = st.tabs(["📊 MIS Automation", "📄 BDC Automation"])

with tab1:
    st.header("Admin MIS Processing")
    st.info("Uses original app.py logic for status classification and zonal mapping.")
    file1 = st.file_uploader("Upload Raw MIS (CSV)", type=["csv"], key="mis_uploader")
    if file1:
        processed_mis = process_car_rental_mis(file1)
        st.success("MIS Processed Successfully!")
        st.dataframe(processed_mis.head(10))
        st.download_button("Download Processed MIS", processed_mis.to_csv(index=False), "MIS_Processed_Report.csv")

with tab2:
    st.header("BDC Automation & Slab Calculation")
    st.info("Includes special 150km package logic and hourly slab calculations.")
    file2 = st.file_uploader("Upload Raw Data for BDC (CSV)", type=["csv"], key="bdc_uploader")
    if file2:
        processed_bdc = process_bdc_automation(file2)
        st.success("BDC Data Generated!")
        
        # Excel buffer for multi-sheet download
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            processed_bdc.to_excel(writer, sheet_name='BDC_Full_Data', index=False)
            summary = processed_bdc.groupby(['Customer', 'Sales Invoice Number']).agg({'Gross Amt': 'sum', 'Booking ID': 'count'}).reset_index()
            summary.to_excel(writer, sheet_name='SUMMARY', index=False)
        
        st.download_button(
            label="⬇️ Download BDC Automation Excel",
            data=output.getvalue(),
            file_name="Automated_BDC_Report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        st.dataframe(processed_bdc.head(10))
