from django import template

from apps.accounts.models import Role

from .. import services as wf

register = template.Library()

# What people actually discuss on each document (the order is about head counts, not menus).
EXAMPLES = {
    "order_submission": "Наприклад: уточніть — на №9 двоє чи один?",
    "order_correction": "Наприклад: пацієнт прийде після обіду, рахуйте з вечері",
    "menu_planning": "Наприклад: перевір меню №9 на обід — замінила кашу",
    "cooking": "Наприклад: закінчився кефір 1% — видали 2,5%",
    "write_off": "Наприклад: ціна на моркву — за новим договором з 01.10",
}


@register.inclusion_tag("workflow/_discussion.html", takes_context=True)
def discussion(context, task):
    """{% discussion task %} under a document: its comments and, for its people, the form."""
    request = context.get("request")
    user = getattr(request, "user", None)
    if task is None or user is None or not user.is_authenticated or not wf.can(user, task, "view"):
        return {"show": False}
    from apps.orgstructure.models import Department

    nurses = [(f"{wf.NURSE_OF}{d.pk}", f"Старша медсестра · {d.name}")
              for d in Department.objects.filter(pk__in=wf.nurse_departments(task))
              if not (user.role == Role.DEPT_NURSE and user.department_id == d.pk)]
    comments = list(wf.discussion(task))
    for c in comments:
        c.addressees = wf.addressee_labels(c)
    return {
        "show": True, "task": task, "comments": comments, "request": request,
        "example": EXAMPLES.get(task.type, "Що уточнити"),
        "csrf_token": context.get("csrf_token"), "max": wf.COMMENT_MAX,
        "roles": nurses + [(r, Role(r).label) for r in wf.CALLABLE_ROLES if r != user.role],
    }
