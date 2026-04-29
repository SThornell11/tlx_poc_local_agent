-- =============================================================================
-- TrustLogix POC — Snowflake setup script (OPTIONAL)
-- Run as ACCOUNTADMIN in a Snowflake worksheet.
--
-- This script is OPTIONAL. The TrustLogix POC agent works with ANY MCP server
-- behind your TLX gateway. Use this script only if you want a self-contained
-- demo dataset to point your gateway at while evaluating the agent.
--
-- It provisions a "POC_DEMO" database with sample HR, Finance, and Engineering
-- data plus a Cortex Agent and a Managed MCP Server (POC_MCP_SERVER) that the
-- TLX gateway sits in front of.
--
-- IMPORTANT: This script does NOT create any masking policies or row-access
-- policies. Those are authored in TrustLogix TrustAccess and pushed down to
-- Snowflake by your TrustLogix admin once the database and roles below are
-- registered. The roles, grants, and tables here are the substrate; the
-- policy layer lives in TrustLogix.
--
-- Default object names (rename here + in the two YAML files if you prefer
-- different conventions — keep them consistent with TLX_GW_SERVICE_ROLE in
-- your .env):
--   POC_DEMO_WH        warehouse
--   POC_DEMO           database
--   POC_TIER1/2/3_ROLE roles TrustLogix will attach masking/row-access policies to
--   POC_GW_SVC         service account the TLX gateway authenticates as
--   POC_MCP_SERVER     managed MCP server the gateway proxies
--   POC_CORTEX_AGENT   Cortex Agent invoked by the MCP tool
--
-- Safe to re-run: uses CREATE ... IF NOT EXISTS / CREATE OR REPLACE where safe.
-- Does NOT delete any existing objects.
-- =============================================================================

-- ─── 1. Warehouse ───────────────────────────────────────────────────────────

CREATE WAREHOUSE IF NOT EXISTS POC_DEMO_WH
    WAREHOUSE_SIZE = 'XSMALL'
    AUTO_SUSPEND = 120
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE;

USE WAREHOUSE POC_DEMO_WH;

-- ─── 2. Database & Schemas ──────────────────────────────────────────────────

CREATE DATABASE IF NOT EXISTS POC_DEMO;

USE DATABASE POC_DEMO;

CREATE SCHEMA IF NOT EXISTS HR;
CREATE SCHEMA IF NOT EXISTS FINANCE;
CREATE SCHEMA IF NOT EXISTS ENGINEERING;
CREATE SCHEMA IF NOT EXISTS CORTEX_OBJECTS;

-- ─── 3. Roles ───────────────────────────────────────────────────────────────

-- Tier roles. The roles themselves grant only USAGE/SELECT on schemas and
-- tables — see "Table Grants" below. Masking and row-access policies are
-- attached to these roles by TrustLogix TrustAccess, NOT by this script.
-- The "tier" labels just describe what TrustLogix will use them for once
-- policies are pushed down (e.g. Tier 1 = full visibility, Tier 3 = heavy
-- masking + row filtering).
CREATE ROLE IF NOT EXISTS POC_TIER1_ROLE;
CREATE ROLE IF NOT EXISTS POC_TIER2_ROLE;
CREATE ROLE IF NOT EXISTS POC_TIER3_ROLE;
-- Admin role for managing demo objects
CREATE ROLE IF NOT EXISTS POC_ADMIN_ROLE;

-- Grant roles to ACCOUNTADMIN so we can use them
GRANT ROLE POC_TIER1_ROLE TO ROLE ACCOUNTADMIN;
GRANT ROLE POC_TIER2_ROLE TO ROLE ACCOUNTADMIN;
GRANT ROLE POC_TIER3_ROLE TO ROLE ACCOUNTADMIN;
GRANT ROLE POC_ADMIN_ROLE TO ROLE ACCOUNTADMIN;

-- ─── 4. Gateway service account ─────────────────────────────────────────────
-- The TrustLogix MCP gateway authenticates to Snowflake as this service user
-- using a key-pair JWT. The gateway holds the matching private key — your
-- TrustLogix contact will provide the corresponding public key body for the
-- ALTER USER statement below.

CREATE USER IF NOT EXISTS POC_GW_SVC
    TYPE = SERVICE
    DEFAULT_WAREHOUSE = 'POC_DEMO_WH'
    DEFAULT_ROLE = 'POC_ADMIN_ROLE'
    COMMENT = 'TrustLogix MCP gateway service account';

-- ─── 5. Register the gateway public key ─────────────────────────────────────
-- Replace <PASTE GATEWAY PUBLIC KEY HERE> with the public-key body provided by
-- TrustLogix (the section between -----BEGIN PUBLIC KEY----- and -----END...,
-- without the BEGIN/END lines or whitespace).

ALTER USER POC_GW_SVC SET RSA_PUBLIC_KEY = '<PASTE GATEWAY PUBLIC KEY HERE>';

-- ─── 6. Role Grants — warehouse, database, schema, future grants ────────────

-- All tier roles get warehouse usage
GRANT USAGE ON WAREHOUSE POC_DEMO_WH TO ROLE POC_TIER1_ROLE;
GRANT USAGE ON WAREHOUSE POC_DEMO_WH TO ROLE POC_TIER2_ROLE;
GRANT USAGE ON WAREHOUSE POC_DEMO_WH TO ROLE POC_TIER3_ROLE;
GRANT USAGE ON WAREHOUSE POC_DEMO_WH TO ROLE POC_ADMIN_ROLE;

-- Database usage
GRANT USAGE ON DATABASE POC_DEMO TO ROLE POC_TIER1_ROLE;
GRANT USAGE ON DATABASE POC_DEMO TO ROLE POC_TIER2_ROLE;
GRANT USAGE ON DATABASE POC_DEMO TO ROLE POC_TIER3_ROLE;
GRANT USAGE ON DATABASE POC_DEMO TO ROLE POC_ADMIN_ROLE;

-- Schema usage — Tier 1 & 2 get all schemas; Tier 3 gets HR + FINANCE only
GRANT USAGE ON SCHEMA POC_DEMO.HR TO ROLE POC_TIER1_ROLE;
GRANT USAGE ON SCHEMA POC_DEMO.HR TO ROLE POC_TIER2_ROLE;
GRANT USAGE ON SCHEMA POC_DEMO.HR TO ROLE POC_TIER3_ROLE;

GRANT USAGE ON SCHEMA POC_DEMO.FINANCE TO ROLE POC_TIER1_ROLE;
GRANT USAGE ON SCHEMA POC_DEMO.FINANCE TO ROLE POC_TIER2_ROLE;
GRANT USAGE ON SCHEMA POC_DEMO.FINANCE TO ROLE POC_TIER3_ROLE;

GRANT USAGE ON SCHEMA POC_DEMO.ENGINEERING TO ROLE POC_TIER1_ROLE;
GRANT USAGE ON SCHEMA POC_DEMO.ENGINEERING TO ROLE POC_TIER2_ROLE;
-- Tier 3 intentionally does NOT get ENGINEERING schema access

GRANT USAGE ON SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_TIER1_ROLE;
GRANT USAGE ON SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_TIER2_ROLE;
GRANT USAGE ON SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_TIER3_ROLE;
GRANT USAGE ON SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_ADMIN_ROLE;

-- Admin gets all schemas
GRANT USAGE ON SCHEMA POC_DEMO.HR TO ROLE POC_ADMIN_ROLE;
GRANT USAGE ON SCHEMA POC_DEMO.FINANCE TO ROLE POC_ADMIN_ROLE;
GRANT USAGE ON SCHEMA POC_DEMO.ENGINEERING TO ROLE POC_ADMIN_ROLE;

-- Grant roles to the gateway service account
GRANT ROLE POC_ADMIN_ROLE TO USER POC_GW_SVC;
GRANT ROLE POC_TIER1_ROLE TO USER POC_GW_SVC;
GRANT ROLE POC_TIER2_ROLE TO USER POC_GW_SVC;
GRANT ROLE POC_TIER3_ROLE TO USER POC_GW_SVC;

-- ─── 7. HR Schema — Employee Data ──────────────────────────────────────────

USE SCHEMA POC_DEMO.HR;

CREATE TABLE IF NOT EXISTS EMPLOYEES (
    EMPLOYEE_ID         NUMBER(10) AUTOINCREMENT START 1001 INCREMENT 1,
    FIRST_NAME          VARCHAR(50),
    LAST_NAME           VARCHAR(50),
    EMAIL               VARCHAR(100),
    SSN                 VARCHAR(11),
    PHONE               VARCHAR(20),
    DATE_OF_BIRTH       DATE,
    HIRE_DATE           DATE,
    JOB_TITLE           VARCHAR(100),
    DEPARTMENT          VARCHAR(50),
    BUSINESS_UNIT       VARCHAR(50),
    REGION              VARCHAR(30),
    ANNUAL_SALARY_USD   NUMBER(10,2),
    MANAGER_ID          NUMBER(10),
    STATUS              VARCHAR(20) DEFAULT 'ACTIVE'
);

-- Only insert data if table is empty (safe for re-runs)
INSERT INTO EMPLOYEES (FIRST_NAME, LAST_NAME, EMAIL, SSN, PHONE, DATE_OF_BIRTH, HIRE_DATE, JOB_TITLE, DEPARTMENT, BUSINESS_UNIT, REGION, ANNUAL_SALARY_USD, MANAGER_ID, STATUS)
SELECT * FROM (
SELECT column1, column2, column3, column4, column5, column6::DATE, column7::DATE, column8, column9, column10, column11, column12::NUMBER(10,2), column13, column14
FROM VALUES
    ('Sophia', 'Martinez', 'sophia.martinez@acmecorp.com', '412-55-7891', '(415) 555-0142', '1988-03-15', '2019-06-01', 'Senior Data Scientist', 'Data Science', 'Engineering', 'US-West', 165000.00, NULL, 'ACTIVE'),
    ('James', 'Chen', 'james.chen@acmecorp.com', '523-66-8902', '(212) 555-0287', '1992-07-22', '2021-01-15', 'ML Engineer', 'Data Science', 'Engineering', 'US-East', 155000.00, 1001, 'ACTIVE'),
    ('Priya', 'Patel', 'priya.patel@acmecorp.com', '634-77-9013', '(408) 555-0331', '1990-11-08', '2020-03-20', 'Product Manager', 'Product', 'Sales', 'US-West', 145000.00, NULL, 'ACTIVE'),
    ('Marcus', 'Johnson', 'marcus.johnson@acmecorp.com', '745-88-0124', '(312) 555-0459', '1985-01-30', '2017-09-12', 'VP Engineering', 'Engineering', 'Engineering', 'US-Central', 210000.00, NULL, 'ACTIVE'),
    ('Elena', 'Volkov', 'elena.volkov@acmecorp.com', '856-99-1235', '(617) 555-0578', '1993-05-17', '2022-02-01', 'Financial Analyst', 'Finance', 'Finance', 'US-East', 95000.00, NULL, 'ACTIVE'),
    ('David', 'Kim', 'david.kim@acmecorp.com', '967-00-2346', '(206) 555-0634', '1991-09-25', '2020-07-10', 'DevOps Lead', 'Infrastructure', 'Engineering', 'US-West', 170000.00, 1004, 'ACTIVE'),
    ('Aisha', 'Ibrahim', 'aisha.ibrahim@acmecorp.com', '178-11-3457', '(305) 555-0721', '1987-12-03', '2018-04-22', 'HR Director', 'Human Resources', 'HR', 'US-East', 135000.00, NULL, 'ACTIVE'),
    ('Carlos', 'Rivera', 'carlos.rivera@acmecorp.com', '289-22-4568', '(512) 555-0845', '1994-06-14', '2023-01-09', 'Sales Engineer', 'Sales', 'Sales', 'US-Central', 120000.00, NULL, 'ACTIVE'),
    ('Hannah', 'O''Brien', 'hannah.obrien@acmecorp.com', '390-33-5679', '(503) 555-0912', '1989-08-19', '2019-11-15', 'Security Engineer', 'Security', 'Engineering', 'US-West', 160000.00, 1004, 'ACTIVE'),
    ('Wei', 'Zhang', 'wei.zhang@acmecorp.com', '401-44-6780', '(650) 555-0198', '1986-02-28', '2016-08-05', 'Principal Architect', 'Engineering', 'Engineering', 'US-West', 225000.00, NULL, 'ACTIVE'),
    ('Rachel', 'Thompson', 'rachel.thompson@acmecorp.com', '512-55-7891', '(720) 555-0267', '1991-04-11', '2021-06-14', 'Data Engineer', 'Data Science', 'Engineering', 'US-Central', 140000.00, 1001, 'ACTIVE'),
    ('Omar', 'Hassan', 'omar.hassan@acmecorp.com', '623-66-8902', '(202) 555-0345', '1988-10-05', '2019-02-28', 'Compliance Manager', 'Legal', 'Finance', 'US-East', 125000.00, NULL, 'ACTIVE'),
    ('Lisa', 'Nakamura', 'lisa.nakamura@acmecorp.com', '734-77-9013', '(858) 555-0423', '1995-01-22', '2023-05-01', 'Junior Developer', 'Engineering', 'Engineering', 'US-West', 95000.00, 1004, 'ACTIVE'),
    ('Robert', 'Williams', 'robert.williams@acmecorp.com', '845-88-0124', '(404) 555-0567', '1983-07-09', '2015-03-17', 'CFO', 'Finance', 'Finance', 'US-East', 280000.00, NULL, 'ACTIVE'),
    ('Meera', 'Sharma', 'meera.sharma@acmecorp.com', '956-99-1235', '(469) 555-0689', '1990-12-30', '2020-09-01', 'QA Lead', 'Quality', 'Engineering', 'US-Central', 130000.00, 1004, 'ACTIVE'),
    ('Tyler', 'Anderson', 'tyler.anderson@acmecorp.com', '167-00-2346', '(303) 555-0734', '1992-03-18', '2021-04-12', 'Account Executive', 'Sales', 'Sales', 'US-West', 110000.00, NULL, 'ACTIVE'),
    ('Fatima', 'Al-Rashid', 'fatima.alrashid@acmecorp.com', '278-11-3457', '(713) 555-0856', '1986-08-24', '2017-11-20', 'VP Sales', 'Sales', 'Sales', 'US-Central', 195000.00, NULL, 'ACTIVE'),
    ('Brandon', 'Lee', 'brandon.lee@acmecorp.com', '389-22-4568', '(415) 555-0923', '1993-11-07', '2022-08-15', 'Backend Engineer', 'Engineering', 'Engineering', 'US-West', 150000.00, 1004, 'ACTIVE'),
    ('Natalia', 'Petrova', 'natalia.petrova@acmecorp.com', '490-33-5679', '(212) 555-0101', '1989-06-02', '2019-05-20', 'Marketing Director', 'Marketing', 'Sales', 'US-East', 155000.00, NULL, 'ACTIVE'),
    ('Kevin', 'Murphy', 'kevin.murphy@acmecorp.com', '501-44-6780', '(617) 555-0234', '1984-09-13', '2016-01-11', 'IT Director', 'IT Operations', 'Engineering', 'US-East', 175000.00, NULL, 'ACTIVE'),
    ('Yuki', 'Tanaka', 'yuki.tanaka@acmecorp.com', '612-55-0891', '(206) 555-0378', '1996-02-14', '2023-09-04', 'Frontend Engineer', 'Engineering', 'Engineering', 'US-West', 125000.00, 1004, 'ACTIVE'),
    ('Sarah', 'Goldman', 'sarah.goldman@acmecorp.com', '723-66-1902', '(312) 555-0445', '1987-05-29', '2018-07-16', 'Controller', 'Finance', 'Finance', 'US-Central', 165000.00, 1014, 'ACTIVE'),
    ('Andre', 'Dubois', 'andre.dubois@acmecorp.com', '834-77-2013', '(305) 555-0512', '1991-10-21', '2020-12-01', 'Platform Engineer', 'Infrastructure', 'Engineering', 'US-East', 155000.00, 1006, 'ACTIVE'),
    ('Chloe', 'Bennett', 'chloe.bennett@acmecorp.com', '945-88-3124', '(503) 555-0689', '1994-04-06', '2022-03-21', 'HR Generalist', 'Human Resources', 'HR', 'US-West', 85000.00, 1007, 'ACTIVE'),
    ('Raj', 'Krishnamurthy', 'raj.krishnamurthy@acmecorp.com', '156-99-4235', '(650) 555-0756', '1988-01-17', '2019-08-05', 'Staff Engineer', 'Engineering', 'Engineering', 'US-West', 190000.00, 1010, 'ACTIVE')
) AS t
WHERE NOT EXISTS (SELECT 1 FROM EMPLOYEES LIMIT 1);

-- ─── 8. FINANCE Schema — Revenue & Budget Data ─────────────────────────────

USE SCHEMA POC_DEMO.FINANCE;

CREATE TABLE IF NOT EXISTS REVENUE (
    REVENUE_ID          NUMBER(10) AUTOINCREMENT START 1 INCREMENT 1,
    FISCAL_YEAR         NUMBER(4),
    FISCAL_QUARTER      VARCHAR(2),
    BUSINESS_UNIT       VARCHAR(50),
    REGION              VARCHAR(30),
    PRODUCT_LINE        VARCHAR(50),
    REVENUE_USD         NUMBER(14,2),
    COGS_USD            NUMBER(14,2),
    GROSS_MARGIN_PCT    NUMBER(5,2)
);

INSERT INTO REVENUE (FISCAL_YEAR, FISCAL_QUARTER, BUSINESS_UNIT, REGION, PRODUCT_LINE, REVENUE_USD, COGS_USD, GROSS_MARGIN_PCT)
SELECT column1, column2, column3, column4, column5, column6::NUMBER(14,2), column7::NUMBER(14,2), column8::NUMBER(5,2)
FROM VALUES
    (2025, 'Q1', 'Engineering', 'US-West', 'Platform Services', 12500000.00, 4375000.00, 65.00),
    (2025, 'Q1', 'Engineering', 'US-East', 'Platform Services', 8900000.00, 3115000.00, 65.00),
    (2025, 'Q1', 'Sales', 'US-West', 'Enterprise Licenses', 18700000.00, 3740000.00, 80.00),
    (2025, 'Q1', 'Sales', 'US-Central', 'Enterprise Licenses', 14200000.00, 2840000.00, 80.00),
    (2025, 'Q1', 'Sales', 'US-East', 'Enterprise Licenses', 16300000.00, 3260000.00, 80.00),
    (2025, 'Q1', 'Finance', 'US-East', 'Financial Services', 5600000.00, 2240000.00, 60.00),
    (2025, 'Q2', 'Engineering', 'US-West', 'Platform Services', 13100000.00, 4585000.00, 65.00),
    (2025, 'Q2', 'Engineering', 'US-East', 'Platform Services', 9400000.00, 3290000.00, 65.00),
    (2025, 'Q2', 'Sales', 'US-West', 'Enterprise Licenses', 19800000.00, 3960000.00, 80.00),
    (2025, 'Q2', 'Sales', 'US-Central', 'Enterprise Licenses', 15100000.00, 3020000.00, 80.00),
    (2025, 'Q2', 'Sales', 'US-East', 'Enterprise Licenses', 17400000.00, 3480000.00, 80.00),
    (2025, 'Q2', 'Finance', 'US-East', 'Financial Services', 5900000.00, 2360000.00, 60.00),
    (2025, 'Q3', 'Engineering', 'US-West', 'Platform Services', 14200000.00, 4970000.00, 65.00),
    (2025, 'Q3', 'Engineering', 'US-East', 'Platform Services', 10100000.00, 3535000.00, 65.00),
    (2025, 'Q3', 'Sales', 'US-West', 'Enterprise Licenses', 21500000.00, 4300000.00, 80.00),
    (2025, 'Q3', 'Sales', 'US-Central', 'Enterprise Licenses', 16200000.00, 3240000.00, 80.00),
    (2025, 'Q3', 'Sales', 'US-East', 'Enterprise Licenses', 18900000.00, 3780000.00, 80.00),
    (2025, 'Q3', 'Finance', 'US-East', 'Financial Services', 6300000.00, 2520000.00, 60.00),
    (2025, 'Q4', 'Engineering', 'US-West', 'Platform Services', 15500000.00, 5425000.00, 65.00),
    (2025, 'Q4', 'Engineering', 'US-East', 'Platform Services', 11200000.00, 3920000.00, 65.00),
    (2025, 'Q4', 'Sales', 'US-West', 'Enterprise Licenses', 23800000.00, 4760000.00, 80.00),
    (2025, 'Q4', 'Sales', 'US-Central', 'Enterprise Licenses', 17800000.00, 3560000.00, 80.00),
    (2025, 'Q4', 'Sales', 'US-East', 'Enterprise Licenses', 20600000.00, 4120000.00, 80.00),
    (2025, 'Q4', 'Finance', 'US-East', 'Financial Services', 6800000.00, 2720000.00, 60.00),
    (2026, 'Q1', 'Engineering', 'US-West', 'Platform Services', 16200000.00, 5670000.00, 65.00),
    (2026, 'Q1', 'Engineering', 'US-East', 'Platform Services', 11800000.00, 4130000.00, 65.00),
    (2026, 'Q1', 'Sales', 'US-West', 'Enterprise Licenses', 25100000.00, 5020000.00, 80.00),
    (2026, 'Q1', 'Sales', 'US-Central', 'Enterprise Licenses', 18900000.00, 3780000.00, 80.00),
    (2026, 'Q1', 'Sales', 'US-East', 'Enterprise Licenses', 22100000.00, 4420000.00, 80.00),
    (2026, 'Q1', 'Finance', 'US-East', 'Financial Services', 7200000.00, 2880000.00, 60.00)
WHERE NOT EXISTS (SELECT 1 FROM REVENUE LIMIT 1);

CREATE TABLE IF NOT EXISTS BUDGETS (
    BUDGET_ID           NUMBER(10) AUTOINCREMENT START 1 INCREMENT 1,
    FISCAL_YEAR         NUMBER(4),
    BUSINESS_UNIT       VARCHAR(50),
    DEPARTMENT          VARCHAR(50),
    BUDGET_CATEGORY     VARCHAR(50),
    BUDGETED_USD        NUMBER(14,2),
    ACTUAL_USD          NUMBER(14,2),
    VARIANCE_PCT        NUMBER(5,2)
);

INSERT INTO BUDGETS (FISCAL_YEAR, BUSINESS_UNIT, DEPARTMENT, BUDGET_CATEGORY, BUDGETED_USD, ACTUAL_USD, VARIANCE_PCT)
SELECT column1, column2, column3, column4, column5::NUMBER(14,2), column6::NUMBER(14,2), column7::NUMBER(5,2)
FROM VALUES
    (2026, 'Engineering', 'R&D', 'Personnel', 8500000.00, 8925000.00, 5.00),
    (2026, 'Engineering', 'R&D', 'Infrastructure', 3200000.00, 3840000.00, 20.00),
    (2026, 'Engineering', 'R&D', 'Tooling', 1500000.00, 1575000.00, 5.00),
    (2026, 'Sales', 'Sales Ops', 'Personnel', 6200000.00, 6510000.00, 5.00),
    (2026, 'Sales', 'Sales Ops', 'Travel', 2100000.00, 2415000.00, 15.00),
    (2026, 'Sales', 'Marketing', 'Campaigns', 4800000.00, 4560000.00, -5.00),
    (2026, 'Finance', 'Accounting', 'Personnel', 2800000.00, 2744000.00, -2.00),
    (2026, 'Finance', 'Accounting', 'Audit', 900000.00, 1035000.00, 15.00),
    (2026, 'HR', 'HR Ops', 'Personnel', 1800000.00, 1854000.00, 3.00),
    (2026, 'HR', 'HR Ops', 'Benefits', 5500000.00, 5775000.00, 5.00),
    (2026, 'Engineering', 'Security', 'Personnel', 3100000.00, 3410000.00, 10.00),
    (2026, 'Engineering', 'Security', 'Tooling', 1200000.00, 1380000.00, 15.00)
WHERE NOT EXISTS (SELECT 1 FROM BUDGETS LIMIT 1);

-- Employees view in Finance (for row-access policy demos)
CREATE TABLE IF NOT EXISTS EMPLOYEES (
    EMPLOYEE_ID         NUMBER(10),
    FULL_NAME           VARCHAR(100),
    EMAIL               VARCHAR(100),
    BUSINESS_UNIT       VARCHAR(50),
    REGION              VARCHAR(30),
    JOB_TITLE           VARCHAR(100),
    ANNUAL_SALARY_USD   NUMBER(10,2),
    COST_CENTER         VARCHAR(20),
    HIRE_DATE           DATE
);

INSERT INTO EMPLOYEES (EMPLOYEE_ID, FULL_NAME, EMAIL, BUSINESS_UNIT, REGION, JOB_TITLE, ANNUAL_SALARY_USD, COST_CENTER, HIRE_DATE)
SELECT column1, column2, column3, column4, column5, column6, column7::NUMBER(10,2), column8, column9::DATE
FROM VALUES
    (1001, 'Sophia Martinez', 'sophia.martinez@acmecorp.com', 'Engineering', 'US-West', 'Senior Data Scientist', 165000.00, 'CC-ENG-100', '2019-06-01'),
    (1002, 'James Chen', 'james.chen@acmecorp.com', 'Engineering', 'US-East', 'ML Engineer', 155000.00, 'CC-ENG-100', '2021-01-15'),
    (1003, 'Priya Patel', 'priya.patel@acmecorp.com', 'Sales', 'US-West', 'Product Manager', 145000.00, 'CC-SAL-200', '2020-03-20'),
    (1004, 'Marcus Johnson', 'marcus.johnson@acmecorp.com', 'Engineering', 'US-Central', 'VP Engineering', 210000.00, 'CC-ENG-100', '2017-09-12'),
    (1005, 'Elena Volkov', 'elena.volkov@acmecorp.com', 'Finance', 'US-East', 'Financial Analyst', 95000.00, 'CC-FIN-300', '2022-02-01'),
    (1006, 'David Kim', 'david.kim@acmecorp.com', 'Engineering', 'US-West', 'DevOps Lead', 170000.00, 'CC-ENG-100', '2020-07-10'),
    (1007, 'Aisha Ibrahim', 'aisha.ibrahim@acmecorp.com', 'HR', 'US-East', 'HR Director', 135000.00, 'CC-HR-400', '2018-04-22'),
    (1008, 'Carlos Rivera', 'carlos.rivera@acmecorp.com', 'Sales', 'US-Central', 'Sales Engineer', 120000.00, 'CC-SAL-200', '2023-01-09'),
    (1009, 'Hannah O''Brien', 'hannah.obrien@acmecorp.com', 'Engineering', 'US-West', 'Security Engineer', 160000.00, 'CC-ENG-100', '2019-11-15'),
    (1010, 'Wei Zhang', 'wei.zhang@acmecorp.com', 'Engineering', 'US-West', 'Principal Architect', 225000.00, 'CC-ENG-100', '2016-08-05'),
    (1014, 'Robert Williams', 'robert.williams@acmecorp.com', 'Finance', 'US-East', 'CFO', 280000.00, 'CC-FIN-300', '2015-03-17'),
    (1017, 'Fatima Al-Rashid', 'fatima.alrashid@acmecorp.com', 'Sales', 'US-Central', 'VP Sales', 195000.00, 'CC-SAL-200', '2017-11-20')
WHERE NOT EXISTS (SELECT 1 FROM POC_DEMO.FINANCE.EMPLOYEES LIMIT 1);

-- ─── 9. ENGINEERING Schema — Test Results & Projects ────────────────────────

USE SCHEMA POC_DEMO.ENGINEERING;

CREATE TABLE IF NOT EXISTS TEST_RESULTS (
    TEST_ID             NUMBER(10) AUTOINCREMENT START 1 INCREMENT 1,
    PRODUCT_ID          VARCHAR(20),
    PRODUCT_NAME        VARCHAR(100),
    TEST_DATE           DATE,
    FISCAL_QUARTER      VARCHAR(7),
    SITE                VARCHAR(50),
    TEST_TYPE           VARCHAR(50),
    RESULT              VARCHAR(10),
    DEFECT_COUNT        NUMBER(5),
    ENGINEER            VARCHAR(100),
    NOTES               VARCHAR(500)
);

INSERT INTO TEST_RESULTS (PRODUCT_ID, PRODUCT_NAME, TEST_DATE, FISCAL_QUARTER, SITE, TEST_TYPE, RESULT, DEFECT_COUNT, ENGINEER, NOTES)
SELECT column1, column2, column3::DATE, column4, column5, column6, column7, column8, column9, column10
FROM VALUES
    ('PRD-000123', 'DataSync Platform', '2025-02-15', '2025-Q1', 'San Jose Lab', 'Integration', 'PASS', 0, 'Sophia Martinez', 'Full regression passed'),
    ('PRD-000123', 'DataSync Platform', '2025-02-20', '2025-Q1', 'Austin Lab', 'Load Testing', 'PASS', 0, 'David Kim', '10K concurrent connections stable'),
    ('PRD-000456', 'SecureVault API', '2025-03-10', '2025-Q1', 'Boston Lab', 'Security Scan', 'FAIL', 3, 'Hannah O''Brien', 'XSS vulnerabilities in input validation'),
    ('PRD-000456', 'SecureVault API', '2025-03-18', '2025-Q1', 'San Jose Lab', 'Penetration', 'PASS', 0, 'Hannah O''Brien', 'Remediated and retested'),
    ('PRD-000789', 'Analytics Engine', '2025-04-05', '2025-Q2', 'San Jose Lab', 'Performance', 'PASS', 0, 'Wei Zhang', 'Sub-100ms p99 latency achieved'),
    ('PRD-000789', 'Analytics Engine', '2025-04-12', '2025-Q2', 'Austin Lab', 'Integration', 'FAIL', 2, 'James Chen', 'Timeout in ETL pipeline step 4'),
    ('PRD-000123', 'DataSync Platform', '2025-05-08', '2025-Q2', 'San Jose Lab', 'Regression', 'PASS', 0, 'Sophia Martinez', 'v3.2 release candidate green'),
    ('PRD-000456', 'SecureVault API', '2025-06-14', '2025-Q2', 'Boston Lab', 'Compliance', 'PASS', 0, 'Hannah O''Brien', 'SOC2 Type II controls verified'),
    ('PRD-000789', 'Analytics Engine', '2025-07-20', '2025-Q3', 'Austin Lab', 'Load Testing', 'FAIL', 1, 'David Kim', 'Memory leak under sustained 5K TPS'),
    ('PRD-000123', 'DataSync Platform', '2025-08-03', '2025-Q3', 'San Jose Lab', 'Integration', 'PASS', 0, 'James Chen', 'Multi-region failover verified'),
    ('PRD-000789', 'Analytics Engine', '2025-08-25', '2025-Q3', 'Austin Lab', 'Load Testing', 'PASS', 0, 'David Kim', 'Memory leak fix confirmed, stable at 5K TPS'),
    ('PRD-000456', 'SecureVault API', '2025-09-10', '2025-Q3', 'Boston Lab', 'Security Scan', 'PASS', 0, 'Hannah O''Brien', 'Quarterly scan clean'),
    ('PRD-000123', 'DataSync Platform', '2025-10-15', '2025-Q4', 'San Jose Lab', 'Performance', 'PASS', 0, 'Wei Zhang', 'Latency reduced 15% with new cache layer'),
    ('PRD-000789', 'Analytics Engine', '2025-11-02', '2025-Q4', 'Austin Lab', 'Integration', 'PASS', 0, 'James Chen', 'ETL pipeline v2 fully stable'),
    ('PRD-000456', 'SecureVault API', '2025-11-20', '2025-Q4', 'Boston Lab', 'Penetration', 'FAIL', 1, 'Hannah O''Brien', 'Token replay vulnerability found'),
    ('PRD-000456', 'SecureVault API', '2025-12-05', '2025-Q4', 'San Jose Lab', 'Security Scan', 'PASS', 0, 'Hannah O''Brien', 'Token replay fix verified'),
    ('PRD-001001', 'Edge Gateway', '2025-07-10', '2025-Q3', 'San Jose Lab', 'Integration', 'PASS', 0, 'Raj Krishnamurthy', 'Initial v1.0 validation'),
    ('PRD-001001', 'Edge Gateway', '2025-09-28', '2025-Q3', 'Austin Lab', 'Load Testing', 'FAIL', 2, 'David Kim', 'Connection drops above 2K TPS'),
    ('PRD-001001', 'Edge Gateway', '2025-11-14', '2025-Q4', 'Austin Lab', 'Load Testing', 'PASS', 0, 'David Kim', 'Connection pool fix, stable at 3K TPS'),
    ('PRD-001001', 'Edge Gateway', '2025-12-20', '2025-Q4', 'San Jose Lab', 'Regression', 'PASS', 0, 'Brandon Lee', 'v1.1 release candidate approved')
WHERE NOT EXISTS (SELECT 1 FROM TEST_RESULTS LIMIT 1);

CREATE TABLE IF NOT EXISTS PROJECTS (
    PROJECT_ID          VARCHAR(20),
    PROJECT_NAME        VARCHAR(100),
    LEAD_ENGINEER       VARCHAR(100),
    STATUS              VARCHAR(20),
    START_DATE          DATE,
    TARGET_DATE         DATE,
    BUSINESS_UNIT       VARCHAR(50),
    BUDGET_USD          NUMBER(14,2)
);

INSERT INTO PROJECTS (PROJECT_ID, PROJECT_NAME, LEAD_ENGINEER, STATUS, START_DATE, TARGET_DATE, BUSINESS_UNIT, BUDGET_USD)
SELECT column1, column2, column3, column4, column5::DATE, column6::DATE, column7, column8::NUMBER(14,2)
FROM VALUES
    ('PROJ-001', 'DataSync Platform v3.2', 'Sophia Martinez', 'RELEASED', '2024-09-01', '2025-06-30', 'Engineering', 2400000.00),
    ('PROJ-002', 'SecureVault API v2.0', 'Hannah O''Brien', 'IN_PROGRESS', '2025-01-15', '2025-12-31', 'Engineering', 1800000.00),
    ('PROJ-003', 'Analytics Engine v2', 'James Chen', 'IN_PROGRESS', '2025-03-01', '2026-03-31', 'Engineering', 3100000.00),
    ('PROJ-004', 'Edge Gateway v1.1', 'Raj Krishnamurthy', 'RELEASED', '2025-05-01', '2025-12-31', 'Engineering', 1200000.00),
    ('PROJ-005', 'AI Governance Framework', 'Wei Zhang', 'PLANNING', '2026-01-01', '2026-09-30', 'Engineering', 2800000.00)
WHERE NOT EXISTS (SELECT 1 FROM PROJECTS LIMIT 1);

-- ─── 10. Documents for Cortex Search ────────────────────────────────────────

USE SCHEMA POC_DEMO.CORTEX_OBJECTS;

CREATE TABLE IF NOT EXISTS DOCUMENTS (
    DOC_ID              NUMBER(10) AUTOINCREMENT START 1 INCREMENT 1,
    TITLE               VARCHAR(200),
    CATEGORY            VARCHAR(50),
    CONTENT             VARCHAR(16000),
    AUTHOR              VARCHAR(100),
    PUBLISHED_DATE      DATE,
    CLASSIFICATION      VARCHAR(20) DEFAULT 'INTERNAL'
);

INSERT INTO DOCUMENTS (TITLE, CATEGORY, CONTENT, AUTHOR, PUBLISHED_DATE, CLASSIFICATION)
SELECT column1, column2, column3, column4, column5::DATE, column6
FROM VALUES
    ('Engineering Code Review Guidelines',
     'Engineering',
     'All code changes require peer review before merging to main. Minimum two approvals for production services. Security-sensitive changes require security team sign-off. Performance-critical paths must include benchmark results. Database migrations require DBA review and must be backward-compatible for at least one release cycle. Feature flags should be used for all user-facing changes. Rollback procedures must be documented in the PR description.',
     'Wei Zhang',
     '2025-01-15',
     'INTERNAL'),

    ('CEO AI Strategy Memo — Three-Tier Governance Framework',
     'Executive',
     'Our AI governance follows a three-tier framework designed to balance innovation with responsible data access. Tier 1 (Full Access) is reserved for data scientists and AI leads who need unrestricted access for model training and analysis. Tier 2 (Partial Access) applies to engineers building AI-powered features — they see masked PII and are restricted to their business unit data. Tier 3 (Read-Only) is for consumer-facing AI copilots and chatbots that should never expose raw PII or access engineering systems. This tiered approach ensures every AI agent operates within clearly defined boundaries. TrustLogix enforces these tiers at the data platform layer, not in application code, making policy evasion architecturally impossible.',
     'Alexandra Chen, CEO',
     '2025-03-01',
     'CONFIDENTIAL'),

    ('Data Classification Policy',
     'Compliance',
     'All data must be classified into one of four levels: PUBLIC (no restrictions), INTERNAL (employee access only), CONFIDENTIAL (need-to-know basis), and RESTRICTED (regulatory controls apply). PII data including SSN, email, phone, salary, and date of birth is classified as RESTRICTED. Financial data is CONFIDENTIAL. Engineering test results are INTERNAL. Document classification must be reviewed quarterly. Any data sharing across business units requires VP-level approval and a documented data sharing agreement.',
     'Omar Hassan',
     '2025-02-20',
     'INTERNAL'),

    ('Incident Response Playbook — AI Agent Data Leak',
     'Security',
     'If an AI agent is suspected of leaking sensitive data: 1) Immediately revoke the agent service account credentials via Snowflake ALTER USER ... SET DISABLED = TRUE. 2) Rotate all RSA key pairs associated with the compromised agent. 3) Review TrustLogix audit logs for the past 72 hours. 4) Notify the security team via #security-incidents Slack channel. 5) Engage legal if PII exposure is confirmed. 6) Post-incident review within 48 hours. Prevention: All agents must authenticate via key-pair JWT, never shared passwords. Row access and masking policies enforce data boundaries regardless of agent behavior.',
     'Hannah O''Brien',
     '2025-04-10',
     'CONFIDENTIAL'),

    ('Snowflake Cost Optimization Guide',
     'Engineering',
     'Warehouse sizing recommendations: Use XSMALL for development and testing. Use SMALL for staging workloads. Production analytical queries should use MEDIUM with auto-suspend at 120 seconds. Multi-cluster warehouses for concurrent user scenarios only. Always set AUTO_RESUME = TRUE and INITIALLY_SUSPENDED = TRUE for new warehouses. Monitor credit usage weekly via ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY. Query pruning and clustering keys reduce scan costs by 60-80% on large tables.',
     'David Kim',
     '2025-05-05',
     'INTERNAL'),

    ('Employee Onboarding — AI Tools Access',
     'HR',
     'New employees receive AI tool access based on their role tier assignment. Tier assignment is determined by department and job level: Directors and above in Data Science get Tier 1. All engineers get Tier 2. All other roles default to Tier 3. Access requests go through the IT service desk with manager approval. Tier upgrades require VP approval and a completed AI safety training module. Access reviews happen quarterly aligned with SOC2 compliance cycles. Terminated employees have AI access revoked within 1 hour via automated SCIM deprovisioning.',
     'Aisha Ibrahim',
     '2025-06-01',
     'INTERNAL')
WHERE NOT EXISTS (SELECT 1 FROM DOCUMENTS LIMIT 1);

-- ─── 11. Row Access & Masking Policies ──────────────────────────────────────
-- INTENTIONALLY EMPTY. All masking and row-access policies are created in
-- TrustLogix TrustAccess and pushed down to Snowflake. Do not add policy
-- DDL here — it would fight the policies that TrustLogix manages.

-- ─── 12. Table Grants ──────────────────────────────────────────────────────

-- HR tables
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.HR TO ROLE POC_TIER1_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.HR TO ROLE POC_TIER2_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.HR TO ROLE POC_TIER3_ROLE;

-- Finance tables
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.FINANCE TO ROLE POC_TIER1_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.FINANCE TO ROLE POC_TIER2_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.FINANCE TO ROLE POC_TIER3_ROLE;

-- Engineering tables — Tier 1 and 2 only (Tier 3 gets denied at schema level)
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.ENGINEERING TO ROLE POC_TIER1_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.ENGINEERING TO ROLE POC_TIER2_ROLE;

-- Cortex objects schema
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_TIER1_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_TIER2_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_TIER3_ROLE;

-- Admin gets everything
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.HR TO ROLE POC_ADMIN_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.FINANCE TO ROLE POC_ADMIN_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.ENGINEERING TO ROLE POC_ADMIN_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_ADMIN_ROLE;

-- Future grants so new tables are automatically accessible
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.HR TO ROLE POC_TIER1_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.HR TO ROLE POC_TIER2_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.HR TO ROLE POC_TIER3_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.FINANCE TO ROLE POC_TIER1_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.FINANCE TO ROLE POC_TIER2_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.FINANCE TO ROLE POC_TIER3_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.ENGINEERING TO ROLE POC_TIER1_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.ENGINEERING TO ROLE POC_TIER2_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_TIER1_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_TIER2_ROLE;
GRANT SELECT ON FUTURE TABLES IN SCHEMA POC_DEMO.CORTEX_OBJECTS TO ROLE POC_TIER3_ROLE;

-- ─── 14. Cortex Search Service ──────────────────────────────────────────────

USE SCHEMA POC_DEMO.CORTEX_OBJECTS;

CREATE OR REPLACE CORTEX SEARCH SERVICE POC_DOC_SEARCH
    ON CONTENT
    ATTRIBUTES TITLE, CATEGORY, AUTHOR, CLASSIFICATION
    WAREHOUSE = POC_DEMO_WH
    TARGET_LAG = '1 hour'
    AS (
        SELECT
            DOC_ID,
            TITLE,
            CATEGORY,
            CONTENT,
            AUTHOR,
            CLASSIFICATION,
            PUBLISHED_DATE
        FROM POC_DEMO.CORTEX_OBJECTS.DOCUMENTS
    );

-- Grant Cortex Search usage to tier roles
GRANT USAGE ON CORTEX SEARCH SERVICE POC_DEMO.CORTEX_OBJECTS.POC_DOC_SEARCH TO ROLE POC_TIER1_ROLE;
GRANT USAGE ON CORTEX SEARCH SERVICE POC_DEMO.CORTEX_OBJECTS.POC_DOC_SEARCH TO ROLE POC_TIER2_ROLE;
GRANT USAGE ON CORTEX SEARCH SERVICE POC_DEMO.CORTEX_OBJECTS.POC_DOC_SEARCH TO ROLE POC_TIER3_ROLE;
GRANT USAGE ON CORTEX SEARCH SERVICE POC_DEMO.CORTEX_OBJECTS.POC_DOC_SEARCH TO ROLE POC_ADMIN_ROLE;

-- ─── 15. Semantic Model Stage ────────────────────────────────────────────────
-- Create a stage for the Cortex Analyst semantic model YAML files

CREATE OR REPLACE STAGE SEMANTIC_MODELS
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'Stage for Cortex Analyst semantic model YAML files';

-- Upload the two YAML files (from the scripts/ folder of this kit) to this stage:
--
--   PUT file://finance_model.yaml @POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
--   PUT file://hr_model.yaml      @POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
--
-- Or via Snowsight: Data > POC_DEMO > CORTEX_OBJECTS > Stages > SEMANTIC_MODELS > + Files

GRANT READ ON STAGE POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS TO ROLE POC_TIER1_ROLE;
GRANT READ ON STAGE POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS TO ROLE POC_TIER2_ROLE;
GRANT READ ON STAGE POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS TO ROLE POC_TIER3_ROLE;
GRANT READ ON STAGE POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS TO ROLE POC_ADMIN_ROLE;

-- ════════════════════════════════════════════════════════════════════════════
-- ▼▼▼ STOP HERE THE FIRST TIME ▼▼▼
--
-- Before you run the CREATE AGENT statement below, upload the two semantic
-- model YAMLs into the @SEMANTIC_MODELS stage you just created:
--
--   scripts/finance_model.yaml  → @POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS
--   scripts/hr_model.yaml       → @POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS
--
-- Easiest: Snowsight UI →
--   Data → POC_DEMO → CORTEX_OBJECTS → Stages → SEMANTIC_MODELS → + Files
--
-- Or via SnowSQL:
--   PUT file://finance_model.yaml @POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS
--      AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
--   PUT file://hr_model.yaml      @POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS
--      AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
--
-- Then continue with the rest of this script.
-- ════════════════════════════════════════════════════════════════════════════

-- ─── 16. Cortex Agent ───────────────────────────────────────────────────────
-- Uses CREATE AGENT ... FROM SPECIFICATION $$ YAML $$
-- Requires both YAML files to already be uploaded to @SEMANTIC_MODELS.

CREATE OR REPLACE AGENT POC_CORTEX_AGENT
  COMMENT = 'TrustLogix POC Cortex Agent — finance, HR, engineering, and document search'
  FROM SPECIFICATION $$
models:
  orchestration: claude-4-sonnet

orchestration:
  budget:
    seconds: 60
    tokens: 32000

instructions:
  system: "You are a TrustLogix enterprise data assistant. You help employees query financial data, HR information, engineering test results, and internal documents. Always respect the user's access tier. Data masking and row filtering are enforced by Snowflake policies automatically. Be concise and accurate. When presenting financial data, format numbers with appropriate units (K, M, B). When PII columns return masked values, acknowledge that the data is masked per policy."
  orchestration: "For revenue, budget, cost, or financial questions use the finance_analyst tool. For employee counts, salary, headcount, PII lookups, or HR questions use the hr_analyst tool. For engineering specs, policy documents, memos, or guidelines use the document_search tool. If the user asks a question spanning both structured and unstructured data, use both tools and combine the results."
  response: "Respond in a professional but friendly tone. Keep answers concise. If data is masked or filtered due to access policies, note this transparently."
  sample_questions:
    - question: "What was total revenue across all regions in 2025?"
      answer: "I will analyze the revenue data using the finance analyst tool."
    - question: "List 5 employees with name, email, SSN, and salary"
      answer: "I will query the HR data for employee PII."
    - question: "Find engineering guidelines from the knowledge base"
      answer: "I will search the internal documents."
    - question: "Show R&D budget variance for 2026"
      answer: "I will query the budget forecast data."
    - question: "What does the CEO AI strategy memo say about tiering?"
      answer: "I will search the internal documents for the CEO memo."

tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "finance_analyst"
      description: "Query structured financial data including revenue by business unit, region, product line, and budget vs actual spending"
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "hr_analyst"
      description: "Query HR data including employee counts, salary, department analysis, and PII lookups. Masking is enforced by Snowflake policies."
  - tool_spec:
      type: "cortex_search"
      name: "document_search"
      description: "Search engineering guidelines, executive memos, policy documents, and compliance materials"

tool_resources:
  finance_analyst:
    semantic_model_file: "@POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS/finance_model.yaml"
  hr_analyst:
    semantic_model_file: "@POC_DEMO.CORTEX_OBJECTS.SEMANTIC_MODELS/hr_model.yaml"
  document_search:
    name: "POC_DEMO.CORTEX_OBJECTS.POC_DOC_SEARCH"
    max_results: "5"
    id_column: "TITLE"
$$;

-- Grant agent usage
GRANT USAGE ON AGENT POC_DEMO.CORTEX_OBJECTS.POC_CORTEX_AGENT TO ROLE POC_TIER1_ROLE;
GRANT USAGE ON AGENT POC_DEMO.CORTEX_OBJECTS.POC_CORTEX_AGENT TO ROLE POC_TIER2_ROLE;
GRANT USAGE ON AGENT POC_DEMO.CORTEX_OBJECTS.POC_CORTEX_AGENT TO ROLE POC_TIER3_ROLE;
GRANT USAGE ON AGENT POC_DEMO.CORTEX_OBJECTS.POC_CORTEX_AGENT TO ROLE POC_ADMIN_ROLE;

-- ─── 17. Managed MCP Server ─────────────────────────────────────────────────

CREATE OR REPLACE MCP SERVER POC_MCP_SERVER
  FROM SPECIFICATION $$
tools:
  - title: "POC Cortex Agent"
    name: "poc_cortex_agent"
    type: "CORTEX_AGENT_RUN"
    identifier: "POC_DEMO.CORTEX_OBJECTS.POC_CORTEX_AGENT"
    description: >
      Run queries against the POC Cortex Agent which can analyze financial,
      HR, and engineering data and search unstructured documents. Masking
      and row-access policies are enforced based on the calling user tier.

  - title: "SQL Execution Tool"
    name: "poc_sql_executor"
    type: "SYSTEM_EXECUTE_SQL"
    description: >
      Execute SQL queries directly against the POC Snowflake database. All
      masking and row-access policies are enforced. Use for ad-hoc queries
      not covered by the Cortex Agent semantic models.
$$;

-- Grant MCP server usage
GRANT USAGE ON MCP SERVER POC_MCP_SERVER TO ROLE POC_TIER1_ROLE;
GRANT USAGE ON MCP SERVER POC_MCP_SERVER TO ROLE POC_TIER2_ROLE;
GRANT USAGE ON MCP SERVER POC_MCP_SERVER TO ROLE POC_TIER3_ROLE;
GRANT USAGE ON MCP SERVER POC_MCP_SERVER TO ROLE POC_ADMIN_ROLE;

-- ─── 17. Verification Queries ───────────────────────────────────────────────

-- Run these to verify the setup:

SHOW DATABASES LIKE 'POC_DEMO';
SHOW SCHEMAS IN DATABASE POC_DEMO;
SHOW ROLES LIKE 'POC_%';
SHOW USERS LIKE 'POC_%';

SELECT 'HR.EMPLOYEES' AS TABLE_NAME, COUNT(*) AS ROW_COUNT FROM POC_DEMO.HR.EMPLOYEES
UNION ALL
SELECT 'FINANCE.REVENUE', COUNT(*) FROM POC_DEMO.FINANCE.REVENUE
UNION ALL
SELECT 'FINANCE.BUDGETS', COUNT(*) FROM POC_DEMO.FINANCE.BUDGETS
UNION ALL
SELECT 'FINANCE.EMPLOYEES', COUNT(*) FROM POC_DEMO.FINANCE.EMPLOYEES
UNION ALL
SELECT 'ENGINEERING.TEST_RESULTS', COUNT(*) FROM POC_DEMO.ENGINEERING.TEST_RESULTS
UNION ALL
SELECT 'ENGINEERING.PROJECTS', COUNT(*) FROM POC_DEMO.ENGINEERING.PROJECTS
UNION ALL
SELECT 'CORTEX_OBJECTS.DOCUMENTS', COUNT(*) FROM POC_DEMO.CORTEX_OBJECTS.DOCUMENTS;

SHOW MCP SERVERS IN SCHEMA POC_DEMO.CORTEX_OBJECTS;
SHOW AGENTS IN SCHEMA POC_DEMO.CORTEX_OBJECTS;
SHOW CORTEX SEARCH SERVICES IN SCHEMA POC_DEMO.CORTEX_OBJECTS;

-- Tier-by-tier masking sanity check (run each block separately AFTER your
-- TrustLogix admin has pushed down the masking and row-access policies):
--   USE ROLE POC_TIER1_ROLE;
--   SELECT FIRST_NAME, LAST_NAME, EMAIL, SSN, ANNUAL_SALARY_USD FROM POC_DEMO.HR.EMPLOYEES LIMIT 5;
--   USE ROLE POC_TIER2_ROLE;
--   SELECT FIRST_NAME, LAST_NAME, EMAIL, SSN, ANNUAL_SALARY_USD FROM POC_DEMO.HR.EMPLOYEES LIMIT 5;
--   USE ROLE POC_TIER3_ROLE;
--   SELECT FIRST_NAME, LAST_NAME, EMAIL, SSN, ANNUAL_SALARY_USD FROM POC_DEMO.HR.EMPLOYEES LIMIT 5;
--
-- Until policies are pushed from TrustLogix, all three tiers see the same
-- raw data — that's expected. The masking/row-access enforcement only
-- appears after TrustLogix attaches the policies to these roles.
