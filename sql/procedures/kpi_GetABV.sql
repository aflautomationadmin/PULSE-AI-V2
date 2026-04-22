-- ============================================================
-- Procedure : kpi.GetABV
-- KPI        : Average Basket Value (ABV)
--
-- Formula
--   ABV = TotalSalesValue / NetBills
--
--   TotalSalesValue = SUM(NETAMT)  for INVOICETYPE = 'SALES'
--
--   NetBills = COUNT(DISTINCT CASE WHEN UPPER(INVOICETYPE) =  'SALES' THEN INV_CNT END)
--            - COUNT(DISTINCT CASE WHEN UPPER(INVOICETYPE) <> 'SALES' THEN INV_CNT END)
--
-- Parameters
--   Date range   : @date_from / @date_to  (explicit, take precedence)
--                  OR @date_preset        (shorthand, used when explicit dates are NULL)
--   Time grain   : @time_grain            NULL  → single aggregate (no date in GROUP BY)
--                                         AUTO  → procedure picks grain from span
--                                         DAY   → group by calendar day
--                                         WEEK  → group by week start (Monday)
--                                         MONTH → group by month start
--                                         QUARTER → group by quarter start
--   Filters      : all optional (NULL = no filter applied)
--   Group-by     : @group_by — comma-separated dimension names, e.g. 'BRAND,STATE'
--                  column names are whitelisted; unrecognised names are silently skipped
--
-- Auto-grain thresholds (when @time_grain = 'AUTO')
--   span ≤  21 days  → DAY
--   span ≤  90 days  → WEEK
--   span ≤ 730 days  → MONTH
--   span >  730 days → QUARTER
-- ============================================================

CREATE OR ALTER PROCEDURE kpi.GetABV

    -- ── Date range ────────────────────────────────────────────────────────────
    @date_from      DATE          = NULL,
    -- Explicit start date.  Takes precedence over @date_preset when supplied.

    @date_to        DATE          = NULL,
    -- Explicit end date.  Defaults to today when NULL.

    @date_preset    NVARCHAR(20)  = 'MTD',
    -- Shorthand period used only when @date_from is NULL.
    -- Accepted values (case-insensitive):
    --   MTD | QTD | YTD | LAST_7 | LAST_14 | LAST_30 | LAST_MONTH

    -- ── Time granularity ─────────────────────────────────────────────────────
    @time_grain     NVARCHAR(10)  = NULL,
    -- Controls whether a date column is added to SELECT / GROUP BY.
    -- NULL    : fully aggregated — no date axis (one row per dimension combination)
    -- AUTO    : procedure infers grain from the resolved date-range span
    -- DAY     : group by calendar day
    -- WEEK    : group by week start date (Monday)
    -- MONTH   : group by first day of month
    -- QUARTER : group by first day of quarter

    -- ── Dimension filters (all optional — NULL means no filter) ──────────────
    @brand          NVARCHAR(200) = NULL,   -- BRAND            (LIKE match)
    @subbrand       NVARCHAR(200) = NULL,   -- SUBBRAND         (LIKE match)
    @store_name     NVARCHAR(200) = NULL,   -- STORE_NAME       (LIKE match)
    @store_format   NVARCHAR(100) = NULL,   -- STORE_FORMAT     (LIKE match)
    @storecode      NVARCHAR(50)  = NULL,   -- STORECODE        (exact match)
    @channel        NVARCHAR(50)  = NULL,   -- CHANNEL          (exact match)
    @region         NVARCHAR(100) = NULL,   -- REGION           (exact match)
    @state          NVARCHAR(100) = NULL,   -- STATE            (LIKE match)
    @city           NVARCHAR(100) = NULL,   -- CITY             (LIKE match)
    @category       NVARCHAR(100) = NULL,   -- CATEGORY         (LIKE match)
    @subclass       NVARCHAR(100) = NULL,   -- SUBCLASS         (LIKE match)

    -- ── Dimension grouping ───────────────────────────────────────────────────
    @group_by       NVARCHAR(500) = NULL
    -- Comma-separated list of column names to group results by.
    -- Allowed values: BRAND, SUBBRAND, STORE_NAME, STORE_FORMAT, STORECODE,
    --                 CHANNEL, REGION, STATE, CITY, CATEGORY, SUBCLASS
    -- Example: 'BRAND,STATE'  |  'CHANNEL,CATEGORY'  |  NULL (no dimension slice)
    -- Column names NOT in the whitelist are silently ignored (injection guard).

AS
BEGIN
    SET NOCOUNT ON;

    -- ══════════════════════════════════════════════════════════════════════════
    -- STEP 1 — Resolve effective date range
    -- Explicit @date_from / @date_to always win.
    -- When NULL, @date_preset is expanded into a concrete date range.
    -- ══════════════════════════════════════════════════════════════════════════

    DECLARE @d_to   DATE = COALESCE(@date_to, CAST(GETDATE() AS DATE));
    DECLARE @d_from DATE;

    IF @date_from IS NOT NULL
    BEGIN
        -- Caller supplied an explicit start date — use it directly.
        SET @d_from = @date_from;
    END
    ELSE
    BEGIN
        SET @d_from =
            CASE UPPER(TRIM(@date_preset))
                WHEN 'MTD'        THEN DATEFROMPARTS(YEAR(@d_to), MONTH(@d_to), 1)
                WHEN 'QTD'        THEN DATEADD(QUARTER, DATEDIFF(QUARTER, 0, @d_to), 0)
                WHEN 'YTD'        THEN DATEFROMPARTS(YEAR(@d_to), 1, 1)
                WHEN 'LAST_7'     THEN DATEADD(DAY,  -6,  @d_to)
                WHEN 'LAST_14'    THEN DATEADD(DAY,  -13, @d_to)
                WHEN 'LAST_30'    THEN DATEADD(DAY,  -29, @d_to)
                WHEN 'LAST_MONTH' THEN DATEFROMPARTS(
                                           YEAR(DATEADD(MONTH, -1, @d_to)),
                                           MONTH(DATEADD(MONTH, -1, @d_to)),
                                           1)
                ELSE DATEFROMPARTS(YEAR(@d_to), MONTH(@d_to), 1) -- fallback = MTD
            END;

        -- For LAST_MONTH the end date should be the last day of that month,
        -- not today — override @d_to only when it was not explicitly provided.
        IF UPPER(TRIM(@date_preset)) = 'LAST_MONTH' AND @date_to IS NULL
            SET @d_to = EOMONTH(DATEADD(MONTH, -1, CAST(GETDATE() AS DATE)));
    END;

    -- ══════════════════════════════════════════════════════════════════════════
    -- STEP 2 — Resolve time grain
    -- When AUTO: pick grain from the span between @d_from and @d_to.
    -- When NULL: no date column in output (fully aggregated).
    -- ══════════════════════════════════════════════════════════════════════════

    DECLARE @span_days INT = DATEDIFF(DAY, @d_from, @d_to);

    IF UPPER(TRIM(@time_grain)) = 'AUTO'
        SET @time_grain =
            CASE
                WHEN @span_days <=  21  THEN 'DAY'      -- ≤ 3 weeks   → daily
                WHEN @span_days <=  90  THEN 'WEEK'     -- ≤ 1 quarter → weekly
                WHEN @span_days <= 730  THEN 'MONTH'    -- ≤ 2 years   → monthly
                ELSE                         'QUARTER'  -- multi-year  → quarterly
            END;

    -- ══════════════════════════════════════════════════════════════════════════
    -- STEP 3 — Build dynamic SQL fragments
    -- Three fragments built separately, then assembled:
    --   @sel_cols   — SELECT column list
    --   @grp_cols   — GROUP BY column list
    --   @ord_cols   — ORDER BY column list
    -- ══════════════════════════════════════════════════════════════════════════

    DECLARE @sel_cols   NVARCHAR(MAX) = '',
            @grp_cols   NVARCHAR(MAX) = '',
            @ord_cols   NVARCHAR(MAX) = '';

    -- ── 3a. Date bucket column (only when a grain is active) ─────────────────

    DECLARE @date_expr  NVARCHAR(300) = NULL;
    DECLARE @period_lbl NVARCHAR(50)  = NULL;   -- friendly alias for the period column

    IF UPPER(TRIM(@time_grain)) = 'DAY'
    BEGIN
        SET @date_expr  = 'CAST(INV_DATE AS DATE)';
        SET @period_lbl = 'Day';
    END
    ELSE IF UPPER(TRIM(@time_grain)) = 'WEEK'
    BEGIN
        -- Monday of the ISO week containing the invoice date
        SET @date_expr  = 'DATEADD(DAY, 1 - DATEPART(WEEKDAY, INV_DATE), CAST(INV_DATE AS DATE))';
        SET @period_lbl = 'WeekStart';
    END
    ELSE IF UPPER(TRIM(@time_grain)) = 'MONTH'
    BEGIN
        SET @date_expr  = 'DATEFROMPARTS(YEAR(INV_DATE), MONTH(INV_DATE), 1)';
        SET @period_lbl = 'Month';
    END
    ELSE IF UPPER(TRIM(@time_grain)) = 'QUARTER'
    BEGIN
        SET @date_expr  = 'DATEADD(QUARTER, DATEDIFF(QUARTER, 0, INV_DATE), 0)';
        SET @period_lbl = 'Quarter';
    END;

    IF @date_expr IS NOT NULL
    BEGIN
        SET @sel_cols += @date_expr + ' AS ' + @period_lbl + ', ';
        SET @grp_cols += @date_expr + ', ';
        SET @ord_cols += @date_expr + ' ASC, ';
    END;

    -- ── 3b. Requested dimension columns ──────────────────────────────────────
    -- Only column names present in @allowed pass through.
    -- The column name goes into the SQL string (not a parameter slot),
    -- so the whitelist is the injection guard.

    DECLARE @allowed TABLE (col NVARCHAR(50));
    INSERT INTO @allowed (col) VALUES
        ('BRAND'), ('SUBBRAND'),
        ('STORE_NAME'), ('STORE_FORMAT'), ('STORECODE'),
        ('CHANNEL'), ('REGION'), ('STATE'), ('CITY'),
        ('CATEGORY'), ('SUBCLASS');

    IF @group_by IS NOT NULL AND LEN(TRIM(@group_by)) > 0
    BEGIN
        DECLARE @token     NVARCHAR(50),
                @comma_pos INT,
                @remaining NVARCHAR(500);

        SET @remaining = UPPER(TRIM(@group_by)) + ',';

        WHILE LEN(@remaining) > 0
        BEGIN
            SET @comma_pos = CHARINDEX(',', @remaining);
            SET @token     = TRIM(LEFT(@remaining, @comma_pos - 1));
            SET @remaining = SUBSTRING(@remaining, @comma_pos + 1, LEN(@remaining));

            IF LEN(@token) = 0 CONTINUE;

            IF EXISTS (SELECT 1 FROM @allowed WHERE col = @token)
            BEGIN
                SET @sel_cols += @token + ', ';
                SET @grp_cols += @token + ', ';
                -- dimension columns go last in ORDER BY (after period, before ABV)
            END;
            -- Unrecognised token → silently skip (injection guard)
        END;
    END;

    -- ── 3c. KPI metric columns (always appended last) ─────────────────────────
    --
    -- NetBills       = SALES invoices (distinct) minus RETURN invoices (distinct)
    -- TotalSalesValue= SUM(NETAMT) for SALES invoices only
    -- ABV            = TotalSalesValue / NetBills  (NULLIF guards divide-by-zero)
    --
    -- Both component columns are included so the summariser / verifier / citations
    -- pipeline can cross-check the derived ABV figure against its inputs.

    SET @sel_cols +=
        '
        COUNT(DISTINCT CASE WHEN UPPER(INVOICETYPE) =  ''SALES'' THEN INV_CNT END)
      - COUNT(DISTINCT CASE WHEN UPPER(INVOICETYPE) <> ''SALES'' THEN INV_CNT END)
            AS NetBills,

        SUM(CASE WHEN UPPER(INVOICETYPE) = ''SALES'' THEN NETAMT ELSE 0 END)
            AS TotalSalesValue,

        SUM(CASE WHEN UPPER(INVOICETYPE) = ''SALES'' THEN NETAMT ELSE 0 END)
        / NULLIF(
              COUNT(DISTINCT CASE WHEN UPPER(INVOICETYPE) =  ''SALES'' THEN INV_CNT END)
            - COUNT(DISTINCT CASE WHEN UPPER(INVOICETYPE) <> ''SALES'' THEN INV_CNT END),
          0)  AS ABV
        ';

    -- ══════════════════════════════════════════════════════════════════════════
    -- STEP 4 — Assemble and execute the full query
    -- All filter values pass through sp_executesql parameters —
    -- only the whitelisted column names are interpolated into the string.
    -- ══════════════════════════════════════════════════════════════════════════

    DECLARE @sql NVARCHAR(MAX);

    SET @sql = '
    SELECT ' + @sel_cols + '
    FROM   prd.FACT_SALES_AI
    WHERE
        INV_DATE BETWEEN @p_from AND @p_to

        -- Dimension filters (all optional)
        AND (@p_brand        IS NULL OR UPPER(BRAND)        LIKE ''%'' + UPPER(@p_brand)        + ''%'')
        AND (@p_subbrand     IS NULL OR UPPER(SUBBRAND)     LIKE ''%'' + UPPER(@p_subbrand)     + ''%'')
        AND (@p_store_name   IS NULL OR UPPER(STORE_NAME)   LIKE ''%'' + UPPER(@p_store_name)   + ''%'')
        AND (@p_store_format IS NULL OR UPPER(STORE_FORMAT) LIKE ''%'' + UPPER(@p_store_format) + ''%'')
        AND (@p_storecode    IS NULL OR UPPER(STORECODE)    =    UPPER(@p_storecode))
        AND (@p_channel      IS NULL OR UPPER(CHANNEL)      =    UPPER(@p_channel))
        AND (@p_region       IS NULL OR UPPER(REGION)       =    UPPER(@p_region))
        AND (@p_state        IS NULL OR UPPER(STATE)        LIKE ''%'' + UPPER(@p_state)        + ''%'')
        AND (@p_city         IS NULL OR UPPER(CITY)         LIKE ''%'' + UPPER(@p_city)         + ''%'')
        AND (@p_category     IS NULL OR UPPER(CATEGORY)     LIKE ''%'' + UPPER(@p_category)     + ''%'')
        AND (@p_subclass     IS NULL OR UPPER(SUBCLASS)     LIKE ''%'' + UPPER(@p_subclass)     + ''%'')
    '
    -- GROUP BY (omitted entirely when no grain and no dimension grouping)
    + CASE
        WHEN LEN(TRIM(@grp_cols)) > 0
        THEN ' GROUP BY ' + LEFT(@grp_cols, LEN(@grp_cols) - 1)   -- trim trailing comma
        ELSE ''
      END
    -- ORDER BY: period first (chronological), then ABV descending for non-trend results
    + CASE
        WHEN LEN(TRIM(@ord_cols)) > 0
        THEN ' ORDER BY ' + LEFT(@ord_cols, LEN(@ord_cols) - 1)
        ELSE ' ORDER BY ABV DESC'
      END;

    EXEC sp_executesql
        @sql,
        -- Parameter declaration
        N'@p_from          DATE,
          @p_to            DATE,
          @p_brand         NVARCHAR(200),
          @p_subbrand      NVARCHAR(200),
          @p_store_name    NVARCHAR(200),
          @p_store_format  NVARCHAR(100),
          @p_storecode     NVARCHAR(50),
          @p_channel       NVARCHAR(50),
          @p_region        NVARCHAR(100),
          @p_state         NVARCHAR(100),
          @p_city          NVARCHAR(100),
          @p_category      NVARCHAR(100),
          @p_subclass      NVARCHAR(100)',
        -- Parameter values
        @d_from, @d_to,
        @brand, @subbrand,
        @store_name, @store_format, @storecode,
        @channel, @region, @state, @city,
        @category, @subclass;

END;
GO


-- ============================================================
-- USAGE EXAMPLES
-- ============================================================

-- 1. MTD ABV — single aggregate number
EXEC kpi.GetABV
    @date_preset = 'MTD';

-- 2. MTD ABV broken by brand
EXEC kpi.GetABV
    @date_preset = 'MTD',
    @group_by    = 'BRAND';

-- 3. Daily ABV trend — last 2 weeks (explicit grain)
EXEC kpi.GetABV
    @date_preset = 'LAST_14',
    @time_grain  = 'DAY';

-- 4. Daily ABV trend — explicit date range, AUTO grain (span = 14 days → DAY)
EXEC kpi.GetABV
    @date_from  = '2026-04-01',
    @date_to    = '2026-04-15',
    @time_grain = 'AUTO';

-- 5. Monthly ABV trend — explicit date range, AUTO grain (span = 182 days → MONTH)
EXEC kpi.GetABV
    @date_from  = '2025-10-01',
    @date_to    = '2026-03-31',
    @time_grain = 'AUTO';

-- 6. Weekly ABV for ARROW — explicit grain overrides AUTO
EXEC kpi.GetABV
    @date_preset = 'LAST_30',
    @brand       = 'ARROW',
    @time_grain  = 'WEEK';

-- 7. Monthly ABV by brand and state — YTD trend
EXEC kpi.GetABV
    @date_preset = 'YTD',
    @time_grain  = 'MONTH',
    @group_by    = 'BRAND,STATE';

-- 8. ABV for offline channel in Karnataka — MTD aggregate
EXEC kpi.GetABV
    @date_preset = 'MTD',
    @channel     = 'OFFLINE',
    @state       = 'KARNATAKA';

-- 9. Quarterly ABV trend — multi-year range, AUTO grain (span > 730 → QUARTER)
EXEC kpi.GetABV
    @date_from  = '2024-01-01',
    @date_to    = '2026-03-31',
    @time_grain = 'AUTO',
    @group_by   = 'BRAND';
