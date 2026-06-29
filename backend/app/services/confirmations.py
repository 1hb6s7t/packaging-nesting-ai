def approval_confirmation_phrase(solution_id: str, decision: str) -> str:
    action = "APPROVE" if decision == "approved" else "REJECT"
    return f"{action} {solution_id}"


def export_confirmation_phrase(solution_id: str, export_type: str) -> str:
    return f"EXPORT {export_type.upper()} {solution_id}"


def task_confirmation_phrase(task_id: str, action: str) -> str:
    return f"{action.upper()} {task_id}"


def adapter_dictionary_signoff_confirmation(config_id: str) -> str:
    return f"SIGNOFF {config_id}"


def check_confirmation(actual: str | None, expected: str) -> None:
    if actual != expected:
        raise ValueError(f"confirmation phrase required: {expected}")
