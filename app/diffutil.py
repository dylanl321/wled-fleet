from __future__ import annotations

SECRET_KEYS = {"psk", "pass", "pwd", "password", "pin", "token", "secret", "passphrase"}


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SECRET_KEYS:
        return True
    return lowered.endswith("password") or lowered.endswith("passphrase")


def mask_secrets(value: object) -> object:
    if isinstance(value, dict):
        masked: dict = {}
        for key, item in value.items():
            if is_secret_key(str(key)) and item not in (None, ""):
                masked[key] = "••••"
            else:
                masked[key] = mask_secrets(item)
        return masked
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    return value


def json_diff(before: object, after: object, path: str = "") -> list[dict]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict] = []
        for key in sorted(set(before) | set(after), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in before:
                changes.append({"path": child, "op": "add", "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": child, "op": "remove", "before": before[key], "after": None})
            else:
                changes.extend(json_diff(before[key], after[key], child))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        if before == after:
            return []
        if len(before) == len(after) and before:
            nested: list[dict] = []
            for index, (left, right) in enumerate(zip(before, after)):
                nested.extend(json_diff(left, right, f"{path}[{index}]"))
            if nested:
                return nested
        return [{"path": path or "$", "op": "replace", "before": before, "after": after}]
    if before != after:
        return [{"path": path or "$", "op": "replace", "before": before, "after": after}]
    return []
