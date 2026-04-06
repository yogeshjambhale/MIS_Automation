import streamlit as st
import pandas as pd
import numpy as np
import io
import re
from datetime import datetime

# --- SETTINGS & CONFIG ---
st.set_page_config(page_title="Car Rental Automation Suite", layout="wide")

# --- 1. SHARED UTILITY: COLUMN MERGING & CLEANING ---
def consolidate_columns(df):
    """
    Standardizes headers and merges redundant columns to prevent duplicates.
    """
    df.columns = df.columns.str.strip().str.replace(r'\s+', ' ', regex=True)
    df = df.loc[:, ~df.columns.duplicated()].copy()
    
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
            consolidated_series = df[existing_cols].bfill(axis=1).iloc[:, 0]
            df = df.drop(columns=existing_cols)
            df[target_col] = consolidated_series
            
    return df

# --- 2. CUSTOM LOGIC: DUTY DETAIL RENAMING ---
def refine_duty_detail(row):
    """
    Applies city, outstation, and tiered package logic while handling missing values.
    """
    # Safely get values, handling NaNs to prevent 'nan' strings
    city = str(row.get('Pickup City', '')).strip()
    if city.lower() == 'nan': city = ''
    
    duty_type = str(row.get('Duty Type', '')).strip()
    if duty_type.lower() == 'nan': duty_type = ''
    
    package = str(row.get('Duty Package', '')).strip()
    if package.lower() == 'nan': package = ''
    
    state = str(row.get('Pickup State', '')).strip().title()
    if state.lower() == 'nan': state = ''

    special_cities = [
        'Mumbai Suburban District', 'Thane Subdistrict', 'Kalyan Subdistrict', 
        'Vasai Subdistrict', 'Mumbai City District', 'Bhiwandi Subdistrict', 'Ulhasnagar Subdistrict'
    ]
    
    # 1. New Condition: Outstation Return for Special Cities
    if city in special_cities and duty_type == "Outstation Return":
        return "250 KM"

    # 2. Special City Logic (Local/Airport)
    if city in special_cities:
        if any(x in duty_type for x in ['Airport Pickup', 'Airport Drop', 'Airport Transfer']):
            return "Airport Transfer"
        return "150 km"

    # 3. Outstation Return Logic for Other Regions
    special_states = ['Assam', 'West Bengal', 'Jammu And Kashmir', 'Uttarakhand', 'Tripura', 'Himachal Pradesh']
    if duty_type == "Outstation Return":
        if state in special_states:
            return "300 KM"
        return "250 KM"

    # 4. Tiered Package Logic (Based on KM values)
    try:
        km_match = re.search(r'(\d+)\s*KM', package, re.IGNORECASE)
        if km_match:
            km_val = int(km_match.group(1))
            if km_val < 60: return "40KM_4HR"
            elif 60 <= km_val < 80: return "60KM_6HR"
            elif 80 <= km_val < 100: return "80KM_8HR"
            elif km_val >= 100: return "100KM_10HR"
    except:
        pass
        
    return package if package else "Local" # Default fallback to fix NaN issues

# --- 3. MIS AUTOMATION LOGIC ---
def process_car_rental_mis(file):
    df = pd.read_csv(file)
    df = consolidate_columns(df)
    if 'Trip Status' in df.columns:
        df = df[df['Trip Status'].str.upper() != 'CANCELLED']
    if 'Labels' in df.columns:
        df['Labels'] = df['Labels'].fillna('')
        unwanted = 'No Bill|Pickup Fail|Duplicate Booking|Vendor No-show'
        df = df[~df['Labels'].str.contains(unwanted, case=False, regex=True)]
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
    return df

# --- 4. BDC & AutoBDC LOGIC ---
def process_bdc_automation(file):
    df = pd.read_csv(file)
    df = consolidate_columns(df)
    df['Duty_Detail_Final'] = df.apply(refine_duty_detail, axis=1)

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

    rates = {'SEDAN': (289, 240, 185), 'SUV': (340, 285, 215), 'PREMIUM_SUV': (500, 415, 330)}
    def apply_billing(row):
        is_special = row['Duty_Detail_Final'] in ["150 km", "Airport Transfer", "250 KM", "300 KM"]
        hrs = row['Total_HRS_Float']
        r1, r2, r3 = rates.get(str(row.get('Vehicle Group', '')).upper(), rates['SEDAN'])
        s1, s2, s3 = min(6.0, hrs), min(6.0, max(0, hrs-6.0)), max(0, hrs-12.0)
        
        if is_special:
            basic = (s1*r1) + (s2*r2) + (s3*r3)
            # Threshold for extra KM varies: 150 for Mumbai Local, otherwise from original data logic
            threshold = 150 if row['Duty_Detail_Final'] == "150 km" else 250
            ex_km_chg = max(0, row.get('Trip Distance(Duty slip-KM)', 0) - threshold) * row.get('Sales Extra Hour Rate', 0)
            return pd.Series([basic, s1, s2, s3, ex_km_chg, 0, s1*r1, s2*r2, s3*r3])
        return pd.Series([row.get('Sales Base Price', 0), s1, s2, s3, row.get('Sales Extra KM Charges', 0), row.get('Sales Extra Hour Charges', 0), 0, 0, 0])

    calc_cols = ['BDC_Basic', '4-6/hrs', '6-12/hrs', '12/hrs & above', 'Ex_Kms_Chg', 'Ex_Hrs_Chg', 'S1_Rate', 'S2_Rate', 'S3_Rate']
    df[calc_cols] = df.apply(apply_billing, axis=1)
    df['Revenue Amt.'] = df['BDC_Basic'] + df['Ex_Kms_Chg'] + df['Ex_Hrs_Chg']
    df['Total Amt'] = df['Revenue Amt.'] + df[['PARKING (Sales)', 'TOLL (Sales)', 'NIGHT_CHARGES (Sales)', 'PERMIT (Sales)']].fillna(0).sum(axis=1)
    df['Gross Amt'] = (df['Total Amt'] * 1.05).round(2)

    # --- AUTO BDC EXACT COLUMN MAPPING (54 COLUMNS) ---
    auto_bdc = pd.DataFrame(index=df.index)
    auto_bdc['Sr. No'] = range(1, len(df) + 1)
    auto_bdc['\xa0Vendor Name '] = 'Aurafox Solution PVT LTD'
    auto_bdc['\xa0Reliance Company Name '] = df.get('Customer', '')
    auto_bdc['\xa0Invoice No. '] = df.get('Sales Invoice Number', '')
    auto_bdc['\xa0Invoice Date '] = df.get('Sales Invoice Date', '')
    auto_bdc['Bill Submission Date\xa0 '] = '2025-12-15'
    auto_bdc['Empl./Guest  Name'] = df.get('Passenger Name', '')
    auto_bdc['Emp. Code'] = df.get('Emp ID', '')
    auto_bdc['Travel ID '] = df.get('Travel ID', '')
    auto_bdc['Trip ID'] = df.get('Trip ID', '')
    auto_bdc['Cost Centre'] = df.get('Cost Center', '')
    auto_bdc['RIL GST Number'] = df.get('GSTN Number', '')
    auto_bdc['Vendor GST num.'] = '27AAOCA9263A1ZL'
    auto_bdc['Travel Date'] = df.get('Trip Start Date', '')
    auto_bdc['End Date'] = df.get('Trip End Date', '')
    auto_bdc['Total Days'] = df.get('Total Trip Days', 1)
    auto_bdc['Dutyslip num.'] = df.get('Booking ID', '')
    auto_bdc['Type of Duty'] = df.get('Duty Type', '')
    auto_bdc['Duty Detail'] = df['Duty_Detail_Final']
    auto_bdc['Vehicle Type'] = df.get('Vehicle Group', '')
    auto_bdc['Basic'] = df.get('BDC_Basic', 0)
    auto_bdc['Car No.'] = df.get('Vehicle Number', '')
    auto_bdc['Car Mfg. Date'] = ""
    auto_bdc['Location'] = df.get('Pickup City', '')
    auto_bdc['To'] = ""
    auto_bdc['Opening KM'] = df.get('Trip Start KM', 0)
    auto_bdc['Closing KM'] = df.get('Trip End KM', 0)
    auto_bdc['Total\xa0Kms'] = df.get('Trip Distance(Duty slip-KM)', 0)
    auto_bdc['Extra Kms'] = 0
    auto_bdc['Ex. Kms Rate'] = df.get('Sales Extra KM Rate', 0)
    auto_bdc['Ex. Kms Charges'] = df.get('Ex_Kms_Chg', 0)
    auto_bdc['Start Timing'] = df.get('Trip Start Time', '')
    auto_bdc['Closing Timing'] = df.get('Trip End Time', '')
    auto_bdc['Total HRS.'] = df.get('Total HRS. Formatted', '')
    auto_bdc['Extra HRS.'] = 0
    auto_bdc['Ex.Hrs. Rate'] = df.get('Sales Extra Hour Rate', 0)
    auto_bdc['Ex. HRS Charges'] = df.get('Ex_Hrs_Chg', 0)
    auto_bdc['4-6/hrs'] = df.get('4-6/hrs', 0)
    auto_bdc['6-12/hrs'] = df.get('6-12/hrs', 0)
    auto_bdc['12/hrs & above'] = df.get('12/hrs & above', 0)
    auto_bdc['Per Hrs Rate      0-6/hrs'] = df.get('S1_Rate', 0)
    auto_bdc['Per Hrs Rate         6-12/hrs'] = df.get('S2_Rate', 0)
    auto_bdc['Per Hrs Rate 12/hrs & above'] = df.get('S3_Rate', 0)
    auto_bdc['Revenue Amt.'] = df.get('Revenue Amt.', 0)
    auto_bdc['Night Allow.'] = df.get('NIGHT_CHARGES (Sales)', 0)
    auto_bdc['Outstation Allow.'] = df.get('DRIVER_CHARGES (Sales)', 0)
    auto_bdc['Parking'] = df.get('PARKING (Sales)', 0)
    auto_bdc['Interstate Tax'] = df.get('PERMIT (Sales)', 0)
    auto_bdc['Toll / Other'] = df.get('TOLL (Sales)', 0)
    auto_bdc['Total Amt'] = df.get('Total Amt', 0)
    auto_bdc['GST/IGST @ 5%'] = (df['Total Amt'] * 0.05).round(2)
    auto_bdc['Gross Amt'] = df.get('Gross Amt', 0)
    auto_bdc['City'] = df.get('Pickup City', '')
    auto_bdc['State'] = df.get('Pickup State', '')

    return df, auto_bdc

# --- 5. STREAMLIT UI ---
st.title("🚗 Car Rental Automation Suite By Yogesh Jambhale")
tab1, tab2, tab3 = st.tabs(["📊 MIS Automation", "📄 BDC Automation", "✨ AutoBDC"])

with tab1:
    st.header("Admin MIS Processing")
    file1 = st.file_uploader("Upload MIS CSV", type=["csv"], key="mis")
    if file1:
        processed_mis = process_car_rental_mis(file1)
        st.success("MIS Processed Successfully!")
        st.dataframe(processed_mis.head(10))
        st.download_button("Download Processed MIS", processed_mis.to_csv(index=False), "MIS_Processed_Report.csv")

with tab2:
    st.header("Detailed BDC Data Analysis")
    file2 = st.file_uploader("Upload Raw Data for BDC (CSV)", type=["csv"], key="bdc")
    if file2:
        df_full, df_auto = process_bdc_automation(file2)
        st.session_state['df_auto'] = df_auto
        st.success("Calculations Complete!")
        st.dataframe(df_full.head(10))

with tab3:
    st.header("AutoBDC: Ready-to-Fill Format")
    if 'df_auto' in st.session_state:
        auto_df = st.session_state['df_auto']
        st.dataframe(auto_df)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            auto_df.to_excel(writer, sheet_name='AutoBDC', index=False)
        st.download_button(label="⬇️ Download AutoBDC Excel", data=output.getvalue(), file_name="AutoBDC_Final.xlsx")
    else:
        st.warning("Please upload a file in the 'BDC Automation' tab first.")
