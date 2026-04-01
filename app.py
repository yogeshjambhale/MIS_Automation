import streamlit as st
import pandas as pd
import numpy as np
import io
from datetime import datetime

# --- SETTINGS ---
st.set_page_config(page_title="Car Rental Automation", layout="wide")

# --- 1. COLUMN MERGING LOGIC ---
def apply_merging_logic(df):
    # Mapping based on your specific requirements
    merging_map = {
        'Trip ID': ['Trip id', 'TRIP ID', 'Trip ID'],
        'Emp ID': ['Emp ID', 'EMP CODE', 'EMP ID', 'EMP .CODE', 'Employee ID', 'EMP. CODE'],
        'GSTN Number': ['GSTN Number', 'GSTN NUMBER', 'GST Number', 'GSTIN NUMBER'],
        'Travel ID': ['TRAVEL ID', 'Travel ID', 'Travel id'],
        'Cost Center': ['Cost Center', 'COST CENTER']
    }
    
    for target, variations in merging_map.items():
        existing = [c for c in variations if c in df.columns]
        if existing:
            # Consolidate: bfill takes the first non-null value across the variants
            df[target] = df[existing].bfill(axis=1).iloc[:, 0]
            # Drop the variants that are not the target column name
            cols_to_drop = [c for c in existing if c != target]
            df.drop(columns=cols_to_drop, inplace=True, errors='ignore')
    return df

# --- 2. BDC CALCULATION LOGIC ---
def process_bdc_logic(df):
    # Time Calculation
    def calculate_duration(row):
        start_str = str(row.get('Trip Start Time', '')).strip()
        end_str = str(row.get('Trip End Time', '')).strip()
        if not start_str or not end_str or 'nan' in start_str.lower(): return 0.0
        try:
            fmt = '%H:%M' if len(start_str.split(':')) == 2 else '%H:%M:%S'
            diff = (datetime.strptime(end_str, fmt) - datetime.strptime(start_str, fmt)).total_seconds() / 3600.0
            return diff + 24.0 if diff < 0 else diff
        except: return 0.0

    df['Total_HRS_Float'] = df.apply(calculate_duration, axis=1)
    df['Total HRS.'] = df['Total_HRS_Float'].apply(lambda x: f"{int(x):02d}:{int((x*60)%60):02d}:00")

    # 150KM & Slab Logic
    special_cities = ['Mumbai Suburban District', 'Thane Subdistrict', 'Kalyan Subdistrict', 
                      'Ulhasnagar Subdistrict', 'Bhiwandi Subdistrict', 'Vasai Subdistrict', 'Mumbai City District']
    rates = {
        'SEDAN': (289, 240, 185), 'SUV': (340, 285, 215), 
        'PREMIUM_SUV': (500, 415, 330), 'HATCHBACK': (200, 150, 100)
    }

    def run_billing(row):
        is_special = (row.get('Pickup City') in special_cities) and (row.get('Duty Type') == 'Daily Rentals')
        hrs = row['Total_HRS_Float']
        vt = str(row.get('Vehicle Group', 'SEDAN')).upper()
        r1, r2, r3 = rates.get(vt, rates['SEDAN'])
        
        # Calculate Slab Breakdown
        s1, s2, s3 = min(6.0, hrs), min(6.0, max(0, hrs-6.0)), max(0, hrs-12.0)
        
        if is_special:
            # Special 150km Logic
            basic = (s1*r1) + (s2*r2) + (s3*r3)
            # Use 'Sales Extra Hour Rate' for KM calculation as requested
            ex_km_chg = max(0, row.get('Trip Distance(Duty slip-KM)', 0) - 150) * row.get('Sales Extra Hour Rate', 0)
            return pd.Series(["150 km", basic, s1, s2, s3, ex_km_chg, 0, s1*r1, s2*r2, s3*r3])
        else:
            # Standard Billing
            return pd.Series([row.get('Duty Package', ''), row.get('Sales Base Price', 0), s1, s2, s3, 
                             row.get('Sales Extra KM Charges', 0), row.get('Sales Extra Hour Charges', 0), 0, 0, 0])

    calc_cols = ['Duty Package', 'Basic', '4-6/hrs', '6-12/hrs', '12/hrs & above', 'Ex. Kms Charges', 'Ex. HRS Charges', 'Per Hr 0-6', 'Per Hr 6-12', 'Per Hr 12+']
    df[calc_cols] = df.apply(run_billing, axis=1)

    # Financial Aggregation
    df['Revenue Amt.'] = df['Basic'] + df['Ex. Kms Charges'] + df['Ex. HRS Charges']
    df['Total Amt'] = df['Revenue Amt.'] + df[['PARKING (Sales)', 'TOLL (Sales)', 'NIGHT_CHARGES (Sales)', 'PERMIT (Sales)']].sum(axis=1)
    df['GST/IGST @ 5%'] = df['Total Amt'] * 0.05
    df['Gross Amt'] = (df['Total Amt'] + df['GST/IGST @ 5%']).round(2)
    
    return df

# --- 3. STREAMLIT UI ---
st.title("🚗 Car Rental Automation - Yogesh Jambhale")
tab1, tab2 = st.tabs(["📊 MIS Automation", "🧾 BDC Automation"])

with tab1:
    st.header("Admin MIS Cleaning & Filtering")
    mis_file = st.file_uploader("Upload MIS CSV", type=["csv"], key="mis")
    if mis_file:
        df_mis = pd.read_csv(mis_file)
        df_mis = apply_merging_logic(df_mis)
        
        # Existing MIS Logic: Filter and Classification
        if 'Trip Status' in df_mis.columns:
            df_mis = df_mis[df_mis['Trip Status'].str.upper() != 'CANCELLED']
        
        st.success("MIS Processed Successfully!")
        st.dataframe(df_mis.head())
        st.download_button("Download Processed MIS", df_mis.to_csv(index=False), "MIS_Processed.csv")

with tab2:
    st.header("BDC Automation & Slab Calculation")
    bdc_file = st.file_uploader("Upload Raw Data for BDC", type=["csv"], key="bdc")
    if bdc_file:
        df_bdc = pd.read_csv(bdc_file)
        df_bdc = apply_merging_logic(df_bdc)
        df_bdc = process_bdc_logic(df_bdc)
        
        st.success("BDC Logic Applied with 150km Slabs!")
        
        # Prepare Excel download
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_bdc.to_excel(writer, sheet_name='BDC', index=False)
            # Create Summary Sheet
            summary = df_bdc.groupby(['Customer', 'Sales Invoice Number']).agg({
                'Gross Amt': 'sum', 'Booking ID': 'count'
            }).reset_index().rename(columns={'Booking ID': 'No of Bookings'})
            summary.to_excel(writer, sheet_name='SUMMARY', index=False)
        
        st.download_button(
            label="⬇️ Download BDC Automation Excel",
            data=output.getvalue(),
            file_name="Automated_BDC_Report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        st.dataframe(df_bdc[['Booking ID', 'Trip ID', 'Duty Package', 'Basic', 'Gross Amt']].head())
