import os
import json
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from langchain_core.prompts.chat import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()


def _get_llm() -> AzureChatOpenAI:
    return AzureChatOpenAI(
        model=os.getenv("AZURE_OPENAI_MODEL", "gpt-4o-mini"),
        openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"),
        api_version="2024-12-01-preview",
        temperature=0,
    )


def generate_report(check_results: dict) -> str:
    """
    Send check results to Azure OpenAI and get a plain-English quality report.
    Falls back to a local summary if credentials are missing.
    """
    required_keys = ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT")
    if not all(os.getenv(k) for k in required_keys):
        return _fallback_report(check_results)

    try:
        profile = check_results.get("dataset_profile", {})
        checks  = check_results.get("checks", [])

        prompt_text = f"""You are a senior data quality analyst writing a report for a business stakeholder.

Dataset overview:
- Source: {profile.get('source')}
- Table: {profile.get('table')}
- Rows: {profile.get('row_count')} | Columns: {profile.get('column_count')}
- Overall status: {check_results.get('overall_status')}
- Run timestamp: {check_results.get('run_timestamp')}

Detailed check results (JSON):
{json.dumps(checks, indent=2)}

Write a data quality report with exactly these four sections:

## Executive Summary
2-3 sentences. State the overall status and the single most critical issue found.

## Issues Found
A bullet point for each FAIL or WARN check. For each:
- Name the specific column(s) affected
- State the actual numbers (e.g. "3 null values in 'country', 25% of rows")
- Explain what the issue means in plain English - no jargon

## Business Impact
2-3 sentences explaining how these specific issues could affect downstream
reporting, dashboards, or business decisions. Be concrete.

## Recommended Actions
A numbered list of specific, actionable fixes - one per issue found.
Include the column name and the exact action to take.

Rules:
- Under 350 words total
- No technical jargon
- Reference actual column names and numbers from the results
- If overall status is PASS, still confirm what was checked and that data looks clean
"""

        chain = (
            ChatPromptTemplate.from_messages([("user", "{prompt}")])
            | _get_llm()
            | StrOutputParser()
        )
        return chain.invoke({"prompt": prompt_text})

    except Exception as e:
        return _fallback_report(check_results, error=str(e))


def _fallback_report(results: dict, error: str = None) -> str:
    """Local report when Azure credentials are missing or the API call fails."""
    profile = results.get("dataset_profile", {})
    checks  = results.get("checks", [])
    overall = results.get("overall_status", "UNKNOWN")

    lines = [
        "## Executive Summary",
        f"Dataset loaded from **{profile.get('source')}** with "
        f"{profile.get('row_count')} rows and {profile.get('column_count')} columns. "
        f"Overall status: **{overall}**.",
        "",
        "## Issues Found",
    ]

    found_issues = False
    for check in checks:
        if check["status"] in ("FAIL", "WARN"):
            found_issues = True
            name = check["check"].replace("_", " ").title()
            lines.append(f"- **{name}** [{check['status']}]: see details panel")

    if not found_issues:
        lines.append("- No issues detected across all checks.")

    lines += [
        "",
        "## Business Impact",
        "Azure OpenAI credentials are configured — re-run the report for full AI analysis.",
        "",
        "## Recommended Actions",
        "1. Ensure your Azure credentials in .env are correct",
        "2. Click 'Generate Report' again to retry",
    ]

    if error:
        lines += ["", f"> API error: `{error}`"]

    return "\n".join(lines)
