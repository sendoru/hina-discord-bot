def tool_config(search_mode: str):
    if search_mode not in {"auto", "required"}:
        return None
    return [{"type": "web_search", "search_context_size": "low"}]
