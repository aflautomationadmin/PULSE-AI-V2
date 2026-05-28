-- ============================================================
-- Procedure : kpi.GetASV
-- KPI        : Average Sales Value (ASV)
--
-- Formula
--   ASV = SUM(NETAMT) / NULLIF(SUM(QTY), 0)
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
-- ============================================================

CREATE OR ALTER PROCEDURE kpi.GetASV

    @date_from      DATE          = NULL,
    @date_to        DATE          = NULL,
    @date_preset    NVARCHAR(20)  = 'MTD',
    @time_grain     NVARCHAR(10)  = NULL,
    @brand          NVARCHAR(200) = NULL,
    @subbrand       NVARCHAR(200) = NULL,
    @store_name     NVARCHAR(200) = NULL,
    @store_format   NVARCHAR(100) = NULL,
    @storecode      NVARCHAR(50)  = NULL,
    @channel        NVARCHAR(50)  = NULL,
    @region         NVARCHAR(100) = NULL,
    @state          NVARCHAR(100) = NULL,
    @city           NVARCHAR(100) = NULL,
    @category       NVARCHAR(100) = NULL,
    @subclass       NVARCHAR(100) = NULL,
    @group_by       NVARCHAR(500) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @d_to   DATE = COALESCE(@date_to, CAST(GETDATE() AS DATE));
    DECLARE @d_from DATE;

    IF @date_from IS NOT NULL
    BEGIN
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
                ELSE DATEFROMPARTS(YEAR(@d_to), MONTH(@d_to), 1)
            END;

        IF UPPER(TRIM(@date_preset)) = 'LAST_MONTH' AND @date_to IS NULL
            SET @d_to = EOMONTH(DATEADD(MONTH, -1, CAST(GETDATE() AS DATE)));
    END;

    DECLARE @span_days INT = DATEDIFF(DAY, @d_from, @d_to);

    IF UPPER(TRIM(@time_grain)) = 'AUTO'
        SET @time_grain =
            CASE
                WHEN @span_days <=  21  THEN 'DAY'
                WHEN @span_days <=  90  THEN 'WEEK'
                WHEN @span_days <= 730  THEN 'MONTH'
                ELSE                         'QUARTER'
            END;

    DECLARE @sel_cols   NVARCHAR(MAX) = '',
            @grp_cols   NVARCHAR(MAX) = '',
            @ord_cols   NVARCHAR(MAX) = '';

    DECLARE @date_expr  NVARCHAR(300) = NULL;
    DECLARE @period_lbl NVARCHAR(50)  = NULL;

    IF UPPER(TRIM(@time_grain)) = 'DAY'
    BEGIN
        SET @date_expr  = 'CAST(INV_DATE AS DATE)';
        SET @period_lbl = 'Day';
    END
    ELSE IF UPPER(TRIM(@time_grain)) = 'WEEK'
    BEGIN
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
            END;
        END;
    END;

    SET @sel_cols +=
        '
        SUM(NETAMT) AS TotalSales,
        SUM(QTY) AS TotalQty,
        SUM(NETAMT) / NULLIF(SUM(QTY), 0) AS ASV
        ';

    DECLARE @sql NVARCHAR(MAX);

    SET @sql = '
    SELECT ' + @sel_cols + '
    FROM   prd.FACT_SALES_AI
    WHERE
        INV_DATE BETWEEN @p_from AND @p_to

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
    ' + CASE
        WHEN LEN(TRIM(@grp_cols)) > 0
        THEN ' GROUP BY ' + LEFT(@grp_cols, LEN(@grp_cols) - 1)
        ELSE ''
      END
    + CASE
        WHEN LEN(TRIM(@ord_cols)) > 0
        THEN ' ORDER BY ' + LEFT(@ord_cols, LEN(@ord_cols) - 1)
        ELSE ' ORDER BY ASV DESC'
      END;

    EXEC sp_executesql
        @sql,
        N'@p_from          DATE,
          @p_to            DATE,
          @p_brand         NVARCHAR(200),
          @p_subbrand      NVARCHAR(200),
          @p_store_name    NVARCHAR(200),
          @p_store_format  NVARCHAR(100),
          @p_storecode     NVARCHAR(50),
          @p_channel      NVARCHAR(50),
          @p_region       NVARCHAR(100),
          @p_state         NVARCHAR(100),
          @p_city          NVARCHAR(100),
          @p_category      NVARCHAR(100),
          @p_subclass     NVARCHAR(100)',
        @d_from, @d_to,
        @brand, @subbrand,
        @store_name, @store_format, @storecode,
        @channel, @region, @state, @city,
        @category, @subclass;
END;
GO
