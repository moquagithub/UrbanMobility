-- ============================================================
-- Mobility Urbain — Schéma MySQL complet
-- Couvre : Automatisation 1 + 2 + Rapport
--
-- Usage :
--   mysql -u <user> -p <database> < shared/db/schema.sql
-- ============================================================

-- ============================================================
-- AUTOMATISATION 1 — Catalogue, Problèmes, Algorithmes
-- ============================================================

-- 1. Imports du catalogue Excel
--    Chaque import est tracé (hash fichier, date, nb types)
CREATE TABLE IF NOT EXISTS catalogue_imports (
    id            INT          NOT NULL AUTO_INCREMENT,
    file_name     VARCHAR(300) NOT NULL,
    file_hash     VARCHAR(64)  NOT NULL COMMENT 'SHA-256 du fichier Excel',
    nb_types      INT          NOT NULL DEFAULT 0,
    imported_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status        ENUM('success','partial','failed') NOT NULL DEFAULT 'success',
    notes         TEXT,
    PRIMARY KEY (id),
    INDEX idx_hash (file_hash),
    INDEX idx_date (imported_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 2. Types de données (depuis catalogue Excel)
CREATE TABLE IF NOT EXISTS data_types (
    id                VARCHAR(100)  NOT NULL,
    name              VARCHAR(300)  NOT NULL,
    description       TEXT,
    specifications    TEXT,
    units             VARCHAR(200),
    frequency         VARCHAR(100),
    catalogue_import_id INT         NULL COMMENT 'Import qui a créé ce type',
    processing_status ENUM(
        'pending',
        'catalogue_done',
        'problems_done',
        'algorithms_done',
        'datasets_done',
        'notebooks_done',
        'report_done'
    ) NOT NULL DEFAULT 'catalogue_done',
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_name   (name(100)),
    INDEX idx_status (processing_status),
    CONSTRAINT fk_dt_import
        FOREIGN KEY (catalogue_import_id) REFERENCES catalogue_imports(id)
        ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 3. Problèmes (3 par type de données, générés par LLM)
CREATE TABLE IF NOT EXISTS problems (
    id           INT          NOT NULL AUTO_INCREMENT,
    data_type_id VARCHAR(100) NOT NULL,
    problem_key  VARCHAR(20)  NOT NULL COMMENT 'p1, p2, p3',
    title        VARCHAR(500) NOT NULL,
    description  TEXT         NOT NULL,
    causes       JSON         NOT NULL COMMENT 'Liste de chaînes',
    consequences TEXT,
    frequency    VARCHAR(50)  DEFAULT 'courant',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE  KEY uq_dtype_key (data_type_id, problem_key),
    INDEX idx_data_type (data_type_id),
    CONSTRAINT fk_prob_dtype
        FOREIGN KEY (data_type_id) REFERENCES data_types(id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 4. Algorithmes (3 par problème, générés par LLM)
CREATE TABLE IF NOT EXISTS algorithms (
    id               INT          NOT NULL AUTO_INCREMENT,
    problem_id       INT          NOT NULL,
    algorithm_key    VARCHAR(20)  NOT NULL COMMENT 'alg1, alg2, alg3',
    name             VARCHAR(300) NOT NULL,
    category         VARCHAR(200),
    principle        TEXT,
    formulation      TEXT,
    complexity_time  VARCHAR(200),
    complexity_space VARCHAR(200),
    advantages       JSON COMMENT 'Liste de chaînes',
    limitations      JSON COMMENT 'Liste de chaînes',
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE  KEY uq_prob_algkey (problem_id, algorithm_key),
    INDEX idx_problem (problem_id),
    CONSTRAINT fk_alg_problem
        FOREIGN KEY (problem_id) REFERENCES problems(id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 5. Log de tous les appels LLM
--    Traçabilité complète : provider, modèle, tokens, coût estimé, durée
CREATE TABLE IF NOT EXISTS llm_calls_log (
    id              INT           NOT NULL AUTO_INCREMENT,
    step            VARCHAR(50)   NOT NULL COMMENT 's2_problems, s3_algorithms, etc.',
    data_type_id    VARCHAR(100)  NULL,
    problem_id      INT           NULL,
    provider        VARCHAR(50)   NOT NULL COMMENT 'Gemini, Groq, Mistral, OpenRouter...',
    model           VARCHAR(200)  NOT NULL,
    tokens_in       INT           NOT NULL DEFAULT 0,
    tokens_out      INT           NOT NULL DEFAULT 0,
    duration_ms     INT           NOT NULL DEFAULT 0,
    success         TINYINT(1)    NOT NULL DEFAULT 1,
    error_message   TEXT          NULL,
    called_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_step        (step),
    INDEX idx_data_type   (data_type_id),
    INDEX idx_provider    (provider),
    INDEX idx_date        (called_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 6. Erreurs de validation JSON LLM
--    Permet de déboguer et d'améliorer les prompts
CREATE TABLE IF NOT EXISTS validation_errors (
    id            INT          NOT NULL AUTO_INCREMENT,
    step          VARCHAR(50)  NOT NULL,
    data_type_id  VARCHAR(100) NULL,
    problem_id    INT          NULL,
    error_type    VARCHAR(100) NOT NULL COMMENT 'missing_key, invalid_format, json_parse...',
    error_detail  TEXT,
    raw_response  MEDIUMTEXT   COMMENT 'Réponse LLM brute ayant échoué la validation',
    attempt       INT          NOT NULL DEFAULT 1,
    resolved      TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '1 = corrigé au retry suivant',
    occurred_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_step       (step),
    INDEX idx_data_type  (data_type_id),
    INDEX idx_error_type (error_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ============================================================
-- AUTOMATISATION 2 — Datasets, Notebooks, Résultats
-- ============================================================

-- 7. Datasets synthétiques (1 générique + 3 problèmes par type)
CREATE TABLE IF NOT EXISTS datasets (
    id            INT          NOT NULL AUTO_INCREMENT,
    data_type_id  VARCHAR(100) NOT NULL,
    problem_id    INT          NULL COMMENT 'NULL = dataset générique',
    dataset_key   VARCHAR(100) NOT NULL DEFAULT 'main',
    description   TEXT,
    n_samples     INT          NOT NULL DEFAULT 0,
    feature_names JSON,
    data_json     LONGTEXT     NOT NULL,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE  KEY uq_dtype_dskey (data_type_id, dataset_key),
    INDEX idx_ds_data_type (data_type_id),
    INDEX idx_ds_problem   (problem_id),
    CONSTRAINT fk_ds_dtype
        FOREIGN KEY (data_type_id) REFERENCES data_types(id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_ds_problem
        FOREIGN KEY (problem_id) REFERENCES problems(id)
        ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 8. Runs d'exécution (un run = une exécution complète par type)
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id               INT          NOT NULL AUTO_INCREMENT,
    project_id       VARCHAR(200) NOT NULL,
    data_type_id     VARCHAR(100),
    automation       ENUM('auto1','auto2','rapport','full') NOT NULL DEFAULT 'full',
    status           ENUM('running','completed','failed','interrupted') NOT NULL DEFAULT 'running',
    started_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at     DATETIME,
    total_problems   INT    DEFAULT 0,
    total_algorithms INT    DEFAULT 0,
    total_notebooks  INT    DEFAULT 0,
    notebooks_ok     INT    DEFAULT 0,
    pdf_path         VARCHAR(500),
    result_json      JSON,
    PRIMARY KEY (id),
    INDEX idx_project   (project_id),
    INDEX idx_data_type (data_type_id),
    INDEX idx_status    (status),
    INDEX idx_automation (automation)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- 9. Résultats d'exécution des notebooks
CREATE TABLE IF NOT EXISTS notebook_results (
    id                  INT          NOT NULL AUTO_INCREMENT,
    run_id              INT          NOT NULL,
    algorithm_id        INT          NULL,
    notebook_num        INT          NOT NULL,
    notebook_path       VARCHAR(500),
    figure_path         VARCHAR(500),
    metrics_json        JSON,
    execution_time_sec  FLOAT,
    status              ENUM('success','partial','failed') NOT NULL DEFAULT 'success',
    cell_errors_count   INT          DEFAULT 0,
    executed_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_run       (run_id),
    INDEX idx_algorithm (algorithm_id),
    CONSTRAINT fk_nb_run
        FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_nb_algorithm
        FOREIGN KEY (algorithm_id) REFERENCES algorithms(id)
        ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
