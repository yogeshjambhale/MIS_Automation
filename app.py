import streamlit as st
import pandas as pd
import numpy as np
import io
from datetime import datetime

# --- SETTINGS & CONFIG ---
st.set_page_config(page_title="Car Rental Automation Tool", layout="wide")

# --- 1. SHARED UTILITIES ---
def merge_columns(df, mapping):
    """Universal column merging logic used by both tools."""
    for target_col, variations in mapping.items():
        existing_cols = [col for col in variations if col in df.columns]
        if existing_cols:
            df[target_col] = df[existing_cols].bfill(axis=1).iloc[:, 0]
            # Optionally drop the variations that aren't the target_col
            # to keep the dataframe clean
            cols_to_drop = [c for c in existing_cols if c != target_col]
            df.drop(columns=cols_to_drop, inplace=True, errors='ignore')
    return df

# --- 2. MIS AUTOMATION LOGIC (Existing) ---
def process_car_rental_mis(file):
    df = pd.read_csv(file)
    
    # Original Merging Logic
    columns_to_merge = {
        'Emp ID': ['Emp ID', 'EMP ID', 'EMP CODE', 'EMP. CODE', 'EMP .CODE', 'Employee ID', 'Emp_ID', 'Employee_ID'],
        'Cost Center': ['Cost Center', 'COST CENTER', 'Cost Centre', 'Cost_Center'],
        'GSTN Number': ['GSTN Number', 'GSTN NUMBER', 'GSTIN NUMBER', 'GSTIN', 'GST_Number'],
        'TRAVEL ID': ['TRAVEL ID', 'Travel id', 'Travel_ID', 'Travel ID'],
        'Trip id': ['Trip id', 'TRIP ID', 'Trip_ID', 'Trip ID']
    }
    df = merge_columns(df, columns_to_merge)

    if 'Trip Status' in df.columns:
        df = df[df['Trip Status'].str.upper() != 'CANCELLED']
    
    if 'Labels' in df.columns:
        df['Labels'] = df['Labels'].fillna('')
        unwanted = 'No Bill|Pickup Fail|Duplicate Booking|Vendor No-show'
        df = df[~df['Labels'].str.contains(unwanted, case=False, regex=True)]
    
    return df

# --- 3. BDC AUTOMATION LOGIC (New) ---
def process_bdc_automation(file):
    df = pd.read_csv(file)
    
    # Updated Merging Logic (as requested)
    bdc_mapping = {
        'Trip ID': ['Trip id', 'TRIP ID', 'Trip ID'],
        'Emp ID': ['Emp ID', 'EMP CODE', 'EMP ID', 'EMP .CODE', 'Employee ID', 'EMP. CODE'],
        'GSTN Number': ['GSTN Number', 'GSTN NUMBER', 'GST Number', 'GSTIN NUMBER'],
        'Travel ID': ['TRAVEL ID', 'Travel ID', 'Travel id'],
        'Cost Center': ['Cost Center', 'COST CENTER']
    }
    df = merge_columns(df, bdc_mapping)

    # Time Duration Logic
    def get_hrs(row):
        start = str(row.get('Trip Start Time', '')).strip()
        end = str(row.get('Trip End Time', '')).strip()
        if not start or not end or 'nan' in start.lower(): return 0.0
        try:
            fmt = '%H:%M' if len(start.split(':')) == 2 else '%H:%M:%S'
            diff = (datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds() / 3600.0
            return diff + 24.0 if diff < 0 else diff
        except: return 0.0

    df['Total_HRS_Float'] = df.apply(get_hrs, axis=1)
    df['Total HRS.'] = df['Total_HRS_Float'].apply(lambda x: f"{int(x):02d}:{int((x*60)%60):02d}:00")

    # 150KM & Slab Logic
    special_cities = ['Mumbai Suburban District', 'Thane Subdistrict', 'Kalyan Subdistrict', 'Ulhasnagar Subdistrict', 'Bhiwandi Subdistrict', 'Vasai Subdistrict', 'Mumbai City District']
    rates = {
        'SEDAN': (289, 240, 185), 'SUV': (340, 285, 215), 'PREMIUM_SUV': (500, 415, 330), 'HATCHBACK': (200, 150, 100)
    }

    def calc_billing(row):
        is_special = (row['Pickup City'] in special_cities) and (row['Duty Type'] == 'Daily Rentals')
        hrs = row['Total_HRS_Float']
        vt = str(row.get('Vehicle Group', 'SEDAN')).upper()
        r1, r2, r3 = rates.get(vt, rates['SEDAN'])
        
        # Slabs
        s1, s2, s3 = min(6.0, hrs), min(6.0, max(0, hrs-6.0)), max(0, hrs-12.0)
        
        if is_special:
            basic = (s1*r1) + (s2*r2) + (s3*r3)
            ex_km_chg = max(0, row.get('Trip Distance(Duty slip-KM)', 0) - 150) * row.get('Sales Extra Hour Rate', 0)
            return pd.Series(["150 km", basic, s1, s2, s3, ex_km_chg, 0, s1*r1, s2*r2, s3*r3])
        else:
            return pd.Series([row.get('Duty Package', ''), row.get('Sales Base Price', 0), s1, s2, s3, 
                             row.get('Sales Extra KM Charges', 0), row.get('Sales Extra Hour Charges', 0), 0, 0, 0])

    cols = ['BDC_Pkg', 'BDC_Basic', '4-6/hrs', '6-12/hrs', '12/hrs & above', 'Ex_Kms_Chg', 'Ex_Hrs_Chg', 'S1_Rate', 'S2_Rate', 'S3_Rate']
    df[cols] = df.apply(calc_billing, axis=1)

    # Financials
    df['Revenue Amt.'] = df['BDC_Basic'] + df['Ex_Kms_Chg'] + df['Ex_Hrs_Chg']
    df['Total Amt'] = df['Revenue Amt.'] + df[['PARKING (Sales)', 'TOLL (Sales)', 'NIGHT_CHARGES (Sales)', 'PERMIT (Sales)']].sum(axis=1)
    df['GST/IGST @ 5%'] = df['Total Amt'] * 0.05
    df['Gross Amt'] = (df['Total Amt'] + df['GST/IGST @ 5%']).round(2)
    
    # State Mapping
    def get_state(c, s):
        c_l = str(c).lower()
        if any(x in c_l for x in ['mumbai', 'thane', 'kalyan', 'pune']): return 'Maharashtra'
        if 'bangalore' in c_l or 'bengaluru' in c_l: return 'Karnataka'
        return s
    df['State'] = df.apply(lambda r: get_state(r['Pickup City'], r['Pickup State']), axis=1)

    return df

# --- 4. STREAMLIT UI ---
st.title("🚗 Car Rental Automation Expert")
tab1, tab2 = st.tabs(["📊 MIS Automation", "📄 BDC Automation"])

with tab1:
    st.header("Admin MIS Processing")
    file1 = st.file_uploader("Upload Raw MIS (CSV)", type=["csv"], key="mis_up")
    if file1:
        out1 = process_car_rental_mis(file1)
        st.success("MIS Processed!")
        st.dataframe(out1.head())
        st.download_button("Download Processed MIS", out1.to_csv(index=False), "MIS_Processed.csv")

with tab2:
    st.header("BDC Generation & Slab Calculation")
    file2 = st.file_uploader("Upload Raw Data for BDC (CSV)", type=["csv"], key="bdc_up")
    if file2:
        out2 = process_bdc_automation(file2)
        st.success("BDC Data Generated with 150km Logic!")
        
        # Prepare Excel with two sheets
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            out2.to_excel(writer, sheet_name='BDC_Full_Data', index=False)
            summary = out2.groupby(['Customer', 'Sales Invoice Number']).agg({'Gross Amt': 'sum', 'Booking ID': 'count'}).reset_index()
            summary.to_excel(writer, sheet_name='SUMMARY', index=False)
        
        st.download_button(
            label="⬇️ Download BDC Automation Excel",
            data=buffer.getvalue(),
            file_name="Automated_BDC_Report.xlsx",
            mime="application/vnd.ms-excel"
        )
        st.dataframe(out2[['Booking ID', 'Pickup City', 'Duty Type', '4-6/hrs', '6-12/hrs', 'Gross Amt']].head(10))
