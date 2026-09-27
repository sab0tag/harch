# 03. Workflow-движок (maker / checker)

## Модели

```python
class TaskStatus(TextChoices):
    PENDING = "pending", "Очікує"
    IN_PROGRESS = "in_progress", "В роботі"
    SUBMITTED = "submitted", "На перевірці"
    APPROVED = "approved", "Затверджено"
    REJECTED = "rejected", "Повернуто на доопрацювання"
    CANCELLED = "cancelled", "Скасовано"

class Task(Model):
    type = CharField(choices=registry.choices())
    status = CharField(choices=TaskStatus.choices, default=PENDING)
    service_date = DateField()
    department = ForeignKey(Department, null=True)     # для задач уровня відділення
    maker_role = CharField();  maker = ForeignKey(User, null=True)    # null = любой пользователь роли
    checker_role = CharField(); checker = ForeignKey(User, null=True)
    content_type + object_id → GenericForeignKey("subject")  # DailyOrder / DailyPlan / WriteOff ...
    parent = ForeignKey("self", null=True)             # предыдущая задача в цепочке
    deadline = DateTimeField(null=True)
    created_at, started_at, submitted_at, resolved_at = DateTimeField(null=True)
    reject_count = PositiveIntegerField(default=0)
    unique_together = (type, service_date, department)

class TaskEvent(Model):                               # неизменяемый журнал
    task = ForeignKey(Task, related_name="events")
    kind = CharField(choices=[created, started, submitted, approved, rejected, cancelled, comment, reassigned])
    actor = ForeignKey(User, null=True)                # null = система
    from_status = CharField(); to_status = CharField()
    comment = TextField(blank=True)
    created_at = DateTimeField(auto_now_add=True)

class Notification(Model):
    user = ForeignKey(User)
    task = ForeignKey(Task, null=True)
    channel = CharField(choices=[in_app, email, telegram])
    title = CharField(); body = TextField()
    created_at; sent_at; read_at
```

## Переходы

| Из | Действие | В | Кто | Условие |
|---|---|---|---|---|
| pending | start | in_progress | maker_role | — (авто при первом сохранении объекта) |
| in_progress / rejected | submit | submitted | maker_role | `definition.validate_submit(subject)` без ошибок |
| submitted | approve | approved | checker_role | user ≠ тот, кто сделал submit |
| submitted | reject | rejected | checker_role | comment обязателен; `reject_count += 1` |
| pending / in_progress | cancel | cancelled | admin | comment обязателен |

Любой другой переход → `InvalidTransition`. Все переходы в `transaction.atomic()` +
`select_for_update()` на Task (две вкладки не утвердят дважды).

## Сервис

```python
# apps/workflow/services.py
def submit(task, user, comment=""): ...
def approve(task, user, comment=""): ...   # после записи → definition.on_approved(task) → может создать следующие задачи
def reject(task, user, comment): ...
def can(user, task, action) -> bool: ...   # единая проверка прав, используется и во view, и в шаблоне
```

## Реестр типов задач — `apps/workflow/registry.py`

```python
@dataclass
class TaskDefinition:
    type: str
    title: str                        # «Замовлення відділення»
    maker_role: str
    checker_role: str
    subject_model: type[Model]
    deadline: Callable[[date], datetime]
    validate_submit: Callable[[Model], list[str]]
    on_approved: Callable[[Task], None]
```

### Типы задач v0.1

| type | Maker | Checker | Создаётся | Дедлайн | on_approved |
|---|---|---|---|---|---|
| `order_submission` | dept_nurse (своё відділення) | dietician | Командой `generate_daily_tasks` для каждого активного відділення на D | D-1 16:00 | Если все `order_submission` на D approved → создать `menu_planning` |
| `menu_planning` | dietician | chef | Автоматически (см. выше) или вручную дієтсестрой после дедлайна | D-1 18:00 | Зафиксировать снапшот (`consumption.services.freeze(plan)`), `plan.approved_at`, создать `cooking` |
| `cooking` | chef | dietician | После approve плана | D 20:00 | Создать `write_off` |
| `write_off` | accountant | dietician | После approve cooking | D+1 12:00 | День закрыт |

`validate_submit` примеры:
- `order_submission`: хотя бы одна строка с порціями > 0, или явная отметка «0 пацієнтів» с комментарием.
- `menu_planning`: для каждой дієти с порціями > 0 выбрано меню этой дієти; меню активно.
- `cooking`: у всех строк `ActualConsumption` с отклонением > 10% заполнена причина.

### Отклонение и повторная подача
При reject объект снова редактируем maker'ом; на экране maker'а сверху красный блок с последним
комментарием checker'а. После исправления — submit, задача возвращается в `submitted` у того же checker'а.

### Дедлайны
Периодическая команда `check_deadlines` (cron каждые 15 мин в контейнере `scheduler`): для
просроченных задач в `pending/in_progress/rejected` создаёт уведомление maker'у и дієтсестре
(один раз на задачу, флаг `overdue_notified_at`). Задача остаётся открытой — просрочка не
блокирует процесс, дієтсестра может сформировать план по поданным замовленням и вручную
отметить «без замовлення».

## Уведомления

v0.1: только `in_app`. Колокольчик в шапке — счётчик непрочитанных, HTMX-опрос
`hx-trigger="every 60s"` на `/notifications/count/`. Выпадающий список последних 10, клик
→ переход на задачу и `read_at`.
Email — если у пользователя заполнен email (v0.1 опционально, через SMTP из `.env`).
Telegram — v0.2.

Кому уведомление:
- создана задача → всем пользователям `maker_role` (для dept_nurse — только своего відділення);
- submit → всем пользователям `checker_role`;
- approve/reject → пользователю, сделавшему submit;
- просрочка → maker'ам + всем dietician.

## Экран «Мої задачі»

Три вкладки: **Мені виконати** (я maker, статус pending/in_progress/rejected) · **Мені перевірити**
(я checker, submitted) · **Історія** (задачи, где я был actor в TaskEvent).
Колонки: тип, дата обслуговування, відділення, статус (бейдж цвета), дедлайн (красный если
просрочен), кнопка «Відкрити».
Карточка задачи: объект (форма или read-only), таймлайн TaskEvent, кнопки действий по `can()`.

## Тесты, которые обязательны

- полный happy path дня: 3 відділення → заказы → план → cooking → write_off, проверка созданных задач и уведомлений;
- reject → правка → resubmit → approve;
- maker не может approve свою задачу;
- dept_nurse не видит задачи чужого відділення (404, не 403);
- двойной approve в параллели не создаёт две следующие задачи;
- после approve плана изменение картки не меняет `ConsumptionSnapshotLine`.
