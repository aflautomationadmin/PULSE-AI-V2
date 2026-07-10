-- ============================================================================
--  Pulse AI — Conversation log + feedback table
--  Target: Microsoft Fabric Warehouse (also valid on SQL Server / Azure SQL)
--
--  One row per chat turn: the question, the answer, how long it took, tokens,
--  and (when the user rates it) the like/dislike + comment.
--
--  Fabric Warehouse notes:
--    • No IDENTITY / DEFAULT  → the app supplies id + created_at.
--    • No ALTER COLUMN        → recreate the table to change a column.
--    • PRIMARY KEY allowed but NOT enforced (informational only).
-- ============================================================================

-- Drop the old feedback-only table if it exists (it is empty on first setup).
DROP TABLE IF EXISTS dbo.PulseAI_Feedback;

CREATE TABLE dbo.PulseAI_Feedback (
    id                VARCHAR(36)   NOT NULL,   -- row id per turn (UUID)
    created_at        DATETIME2(0)  NOT NULL,   -- when the answer was produced
    user_email        VARCHAR(256)  NULL,       -- who asked
    thread_id         VARCHAR(64)   NULL,       -- conversation thread
    trace_id          VARCHAR(64)   NULL,       -- turn key (feedback attaches here)
    route             VARCHAR(32)   NULL,       -- business_question | normal_chat
    question          VARCHAR(4000) NULL,       -- the user's question
    response          VARCHAR(8000) NULL,       -- the bot's answer
    response_time_ms  INT           NULL,       -- how long the answer took (ms)
    total_tokens      INT           NULL,       -- tokens used for this turn
    liked             VARCHAR(10)   NULL,       -- 'like' | 'dislike' | NULL (no rating)
    comment           VARCHAR(4000) NULL        -- feedback comment (optional)
);
