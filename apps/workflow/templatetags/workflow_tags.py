from django import template

from apps.accounts.models import Role

from .. import services as wf

register = template.Library()


@register.inclusion_tag("workflow/_discussion.html", takes_context=True)
def discussion(context, task):
    """{% discussion task %} under a document: its comments and, for its people, the form."""
    request = context.get("request")
    user = getattr(request, "user", None)
    if task is None or user is None or not user.is_authenticated or not wf.can(user, task, "view"):
        return {"show": False}
    return {
        "show": True, "task": task, "comments": list(wf.discussion(task)), "request": request,
        "csrf_token": context.get("csrf_token"), "max": wf.COMMENT_MAX,
        "roles": [(r, Role(r).label) for r in wf.CALLABLE_ROLES if r != user.role],
    }
