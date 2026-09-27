def ai(request):
    """`ai_available` lets templates hide AI buttons when AI is off (the system works without it)."""
    from .client import is_available

    return {"ai_available": is_available()}
