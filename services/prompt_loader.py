from __future__ import annotations

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = BASE_DIR / "prompts"
AGENTS_DIR = PROMPTS_DIR / "agents"


STAGE_PROMPT_FILES = {
    # Fast audit workflow
    "Intake Completeness": "agents/intake_completeness.txt",
    "Risk Assessment": "agents/risk_assessment.txt",
    "Economics Assessment": "agents/economics_assessment.txt",
    "Final Output": "agents/final_output.txt",

    # Full audit workflow — оставляем для расширенного режима
    "Intake": "agents/intake.txt",
    "Qualification": "agents/qualification.txt",
    "Process Audit": "agents/process_audit.txt",
    "Data Readiness": "agents/data_readiness.txt",
    "Use Cases": "agents/use_cases.txt",
    "ROI": "agents/roi.txt",
    "Architecture": "agents/architecture.txt",
    "Risk & Compliance": "agents/risk_compliance.txt",
    "Report": "agents/report.txt",
    "Human Review": "agents/human_review.txt",
    "Commercial Proposal": "agents/commercial_proposal.txt",
}


def load_prompt(relative_path: str) -> str:
    """
    Загружает prompt-файл из папки prompts.
    """
    path = PROMPTS_DIR / relative_path

    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    return path.read_text(encoding="utf-8").strip()


def load_system_prompt() -> str:
    """
    Загружает общий system prompt для AIha workflow agent.
    """
    return load_prompt("system/aiha_workflow_agent.txt")

def load_agent_prompt(agent_name: str) -> str:
    """
    Загружает prompt агента из:

    prompts/agents/<agent_name>.txt

    Пример:

    load_agent_prompt("mvp_design")

    =>
    prompts/agents/mvp_design.txt
    """

    prompt_path = AGENTS_DIR / f"{agent_name}.txt"

    if not prompt_path.exists():
        raise FileNotFoundError(
            f"Agent prompt not found: {prompt_path}"
        )

    return prompt_path.read_text(
        encoding="utf-8"
    ).strip()


def load_stage_prompt(stage: str) -> str:
    """
    Загружает prompt конкретного этапа workflow.
    Если prompt-файл для этапа не найден в словаре, возвращает fallback.
    """
    relative_path = STAGE_PROMPT_FILES.get(stage)

    if not relative_path:
        return f"""
Ты агент этапа {stage} в workflow AIha Consulting.

Обработай входной блок, сформируй результат анализа для поля task.result.
Если данных недостаточно, явно укажи, чего не хватает.
Не выдумывай факты. Разделяй факты, гипотезы и допущения.
""".strip()

    return load_prompt(relative_path)