"""Paths and settings for the quote desk pipeline.

Only the data files the pipeline is allowed to read are defined here.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Explicit path, so a .env in a parent directory is never picked up. Variables already
# set in the shell win over the file, and a missing file is fine.
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
RFQS_DIR = DATA_DIR / "rfqs"
PRODUCTS_CSV = DATA_DIR / "products.csv"
CROSS_REFERENCE_CSV = DATA_DIR / "cross_reference.csv"
CUSTOMERS_CSV = DATA_DIR / "customers.csv"
OUTPUTS_DIR = ROOT / "outputs"
PROMPTS_DIR = ROOT / "ridgeline" / "prompts"

# The extraction cache is committed, so anyone can reproduce the outputs and metrics
# without an API key. In production this cache would live in a US bucket with a
# retention policy.
CACHE_DIR = ROOT / ".cache" / "llm"

EVALUATION_DIR = ROOT / "evaluation"
RUN_STATS_JSON = EVALUATION_DIR / "run_stats.json"

REVIEW_THRESHOLD = 0.80
PROMPT_VERSION = "extract_v1"

# `or` instead of a getenv default, so an empty value (for example copied from
# .env.example) falls back to the default too.
LLM_MODEL = os.getenv("LLM_MODEL") or "claude-sonnet-5-5"
LLM_INFERENCE_GEO = os.getenv("LLM_INFERENCE_GEO") or "us"
