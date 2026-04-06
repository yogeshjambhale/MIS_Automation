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
    Cleans headers and merges columns based on specific mappings.
    Prevents duplicates like 'Emp ID' and 'Emp  ID'.
    """
    df.columns = df.columns.str.strip()
    
    mapping = {
        'Trip ID': ['Trip id', 'TRIP ID', 'Trip ID'],
        'Emp ID': ['Emp ID', 'EMP CODE', 'EMP ID', 'EMP .CODE', 'Employee ID', 'EMP. CODE'],
        'GSTN Number': ['GSTN Number', 'GSTN NUMBER', 'GST Number', 'GSTIN NUMBER'],
        'Travel ID': ['TRAVEL ID', 'Travel ID', 'Travel id'],
        'Cost Center': ['Cost Center', 'COST CENTER']
    }

    for target_col, variations in mapping.items():
        existing_cols = [col for col in variations if col in df.columns]
        if existing_cols:
            df[target_col] = df[existing_cols].bfill(axis=1).iloc[:, 0]
            cols_to_drop = [c for c in existing_cols if c != target_col]
            df.drop(columns=cols_to_drop, inplace=True, errors='ignore')
    return df

# --- 1. MIS AUTOMATION LOGIC (Full Features) ---
def process_car_rental_mis(file):
    df = pd.read_csv(file)
    df = consolidate_columns(df)

    if 'Trip Status' in df.columns:
        df = df[df['Trip Status'].str.upper() != 'CANCELLED']

    if 'Labels' in df.columns:
        df['Labels'] = df['Labels'].fillna('')
        unwanted_labels = 'No Bill|Pickup Fail|Duplicate Booking|Vendor No-show'
        df = df[~df['Labels'].str.contains(unwanted_labels, case=False, regex=True)]

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

    zone_mapping = {
        'Maharashtra': 'West', 'Gujarat': 'West', 'Goa': 'West', 'Karnataka': 'South', 'Tamil Nadu': 'South', 
        'Delhi': 'North', 'Haryana': 'North', 'West Bengal': 'East', 'Bihar': 'East'
    }
    if 'Pickup State' in df.columns:
        df['Pickup State'] = df['Pickup State'].str.strip().str.title()
        df['Zone'] = df['Pickup State'].map(zone_mapping).fillna('Unknown Zone')

    return df

# --- 2. BDC & AutoBDC LOGIC ---
def process_bdc_automation(file):
    df = pd.read_csv(file)
    df = consolidate_columns(df)

    # Time Duration
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
    df['Total HRS. Formatted'] = df['Total_HRS_Float'].apply(lambda x: f"{int(x):02d}:{int((x*60)%60):02d}:00")

    # Slab Rates
    special_cities = ['Mumbai Suburban District', 'Thane Subdistrict', 'Kalyan Subdistrict', 'Vasai Subdistrict', 'Mumbai City District']
    rates = {'SEDAN': (289, 240, 185), 'SUV': (340, 285, 215), 'PREMIUM_SUV': (500, 415, 330)}

    def apply_billing(row):
        is_special = (row.get('Pickup City') in special_cities) and (row.get('Duty Type') == 'Daily Rentals')
        hrs = row['Total_HRS_Float']
        vt = str(row.get('Vehicle Group', 'SEDAN')).upper()
        r1, r2, r3 = rates.get(vt, rates['SEDAN'])
        
        s1, s2, s3 = min(6.0, hrs), min(6.0, max(0, hrs-6.0)), max(0, hrs-12.0)
        
        if is_special:
            basic = (s1*r1) + (s2*r2) + (s3*r3)
            ex_km_chg = max(0, row.get('Trip Distance(Duty slip-KM)', 0) - 150) * row.get('Sales Extra Hour Rate', 0)
            return pd.Series(["150 km", basic, s1, s2, s3, ex_km_chg, 0, s1*r1, s2*r2, s3*r3])
        else:
            return pd.Series([row.get('Duty Package', ''), row.get('Sales Base Price', 0), s1, s2, s3, 
                             row.get('Sales Extra KM Charges', 0), row.get('Sales Extra Hour Charges', 0), 0, 0, 0])

    cols = ['BDC_Package', 'BDC_Basic', '4-6/hrs', '6-12/hrs', '12/hrs & above', 'Ex_Kms_Chg', 'Ex_Hrs_Chg', 'S1_Rate', 'S2_Rate', 'S3_Rate']
    df[cols] = df.apply(apply_billing, axis=1)

    df['Revenue Amt.'] = df['BDC_Basic'] + df['Ex_Kms_Chg'] + df['Ex_Hrs_Chg']
    df['Total Amt'] = df['Revenue Amt.'] + df[['PARKING (Sales)', 'TOLL (Sales)', 'NIGHT_CHARGES (Sales)', 'PERMIT (Sales)']].fillna(0).sum(axis=1)
    df['GST/IGST @ 5%'] = df['Total Amt'] * 0.05
    df['Gross Amt'] = (df['Total Amt'] + df['GST/IGST @ 5%']).round(2)

    # --- AUTO BDC MAPPING ---
    auto_bdc = pd.DataFrame()
    auto_bdc['Sr. No'] = range(1, len(df) + 1)
    auto_bdc['\xa0Vendor Name '] = 'Aurafox Solution PVT LTD'
    auto_bdc['\xa0Reliance Company Name '] = df['Customer']
    auto_bdc['\xa0Invoice No. '] = df['Sales Invoice Number']
    auto_bdc['\xa0Invoice Date '] = df['Sales Invoice Date']
    auto_bdc['Bill Submission Date\xa0 '] = '2025-12-15'
    auto_bdc['Empl./Guest  Name'] = df['Passenger Name']
    auto_bdc['Emp. Code'] = df['Emp ID']
    auto_bdc['Travel ID '] = df['Travel ID']
    auto_bdc['Trip ID'] = df['Trip ID']
    auto_bdc['Cost Centre'] = df['Cost Center']
    auto_bdc['RIL GST Number'] = df['GSTN Number']
    auto_bdc['Vendor GST num.'] = '27AAOCA9263A1ZL'
    auto_bdc['Travel Date'] = df['Trip Start Date']
    auto_bdc['Unnamed: 14'] = df['Trip End Date']
    auto_bdc['Total Days'] = df['Total Trip Days']
    auto_bdc['Dutyslip num.'] = df['Booking ID']
    auto_bdc['Type of Duty'] = df['Duty Type']
    auto_bdc['Unnamed: 18'] = df['BDC_Package']
    auto_bdc['Vehicle Type'] = df['Vehicle Group']
    auto_bdc['Basic'] = df['BDC_Basic']
    auto_bdc['Car No.'] = df['Vehicle Number']
    auto_bdc['Car Mfg. Date'] = ""
    auto_bdc['Location'] = df['Pickup City']
    auto_bdc['Unnamed: 24'] = ""
    auto_bdc['Kilometer'] = df.get('Trip Start KM', 0)
    auto_bdc['Unnamed: 26'] = df.get('Trip End KM', 0)
    auto_bdc['Total\xa0Kms'] = df['Trip Distance(Duty slip-KM)']
    auto_bdc['Extra Kms'] = df['Ex_Kms_Chg'] / df.get('Sales Extra KM Rate', 1) # Estimation
    auto_bdc['Ex. Kms Rate'] = df.get('Sales Extra KM Rate', 0)
    auto_bdc['Ex. Kms Charges'] = df['Ex_Kms_Chg']
    auto_bdc['Pick up Timing'] = df['Trip Start Time']
    auto_bdc['Unnamed: 32'] = df['Trip End Time']
    auto_bdc['Total HRS.'] = df['Total HRS. Formatted']
    auto_bdc['Extra HRS.'] = 0 
    auto_bdc['Ex.Hrs. Rate'] = df.get('Sales Extra Hour Rate', 0)
    auto_bdc['Ex. HRS Charges'] = df['Ex_Hrs_Chg']
    auto_bdc['4-6/hrs'] = df['4-6/hrs']
    auto_bdc['6-12/hrs'] = df['6-12/hrs']
    auto_bdc['12/hrs & above'] = df['12/hrs & above']
    auto_bdc['Per Hrs Rate      0-6/hrs'] = df['S1_Rate']
    auto_bdc['Per Hrs Rate         6-12/hrs'] = df['S2_Rate']
    auto_bdc['Per Hrs Rate 12/hrs & above'] = df['S3_Rate']
    auto_bdc['Revenue Amt.'] = df['Revenue Amt.']
    auto_bdc['Night Allow.'] = df['NIGHT_CHARGES (Sales)']
    auto_bdc['Outstation Allow.'] = df['DRIVER_CHARGES (Sales)']
    auto_bdc['Parking'] = df['PARKING (Sales)']
    auto_bdc['Interstate Tax'] = df['PERMIT (Sales)']
    auto_bdc['Toll / Other'] = df['TOLL (Sales)']
    auto_bdc['Total Amt'] = df['Total Amt']
    auto_bdc['GST/IGST @ 5%'] = df['GST/IGST @ 5%']
    auto_bdc['Gross Amt'] = df['Gross Amt']
    auto_bdc['City'] = df['Pickup City']
    auto_bdc['State'] = df['Pickup State']

    return df, auto_bdc

# --- STREAMLIT UI ---
st.title("🚗 Car Rental Automation Suite")
tab1, tab2, tab3 = st.tabs(["📊 MIS Automation", "📄 BDC Automation", "✨ AutoBDC"])

with tab1:
    st.header("MIS Report Processing")
    file1 = st.file_uploader("Upload MIS CSV", type=["csv"], key="mis")
    if file1:
        processed_mis = process_car_rental_mis(file1)
        st.success("MIS Processed!")
        st.dataframe(processed_mis.head(10))
        st.download_button("Download Processed MIS", processed_mis.to_csv(index=False), "MIS_Processed.csv")

with tab2:
    st.header("Detailed BDC Data")
    file2 = st.file_uploader("Upload BDC Data (CSV)", type=["csv"], key="bdc")
    if file2:
        df_full, df_auto = process_bdc_automation(file2)
        st.success("Full BDC Data Generated!")
        st.dataframe(df_full.head(10))

with tab3:
    st.header("AutoBDC: Ready-to-Fill Format")
    st.info("This tab provides only the exact columns required for the BDC template.")
    if 'df_auto' in locals():
        st.dataframe(df_auto)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_auto.to_excel(writer, sheet_name='AutoBDC', index=False)
            df_full.to_excel(writer, sheet_name='Full_Data', index=False)
        
        st.download_button(
            label="⬇️ Download AutoBDC Formatted Excel",
            data=output.getvalue(),
            file_name="AutoBDC_Ready_Report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("Please upload a file in the BDC Automation tab first.")
